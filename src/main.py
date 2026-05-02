"""
CLI 入口：按药物编排异步流水线，处理 Excel 批量输入。
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.article_filter import (
    drug_in_title_abstract,
    enrich_article_evidence_metadata,
    filter_articles,
    filter_articles_with_pmcid,
    normalize_any_article,
)
from src.context_extractor import extract_keyword_contexts
from src.fulltext_extractor import fetch_fulltext_for_articles
from src.mcp_client import PubMedMCPClient
from src.query_builder import build_simple_query, build_variant_queries, iter_simple_fallbacks, strip_salt_suffix
from src.report_writer import write_json, write_markdown_report

load_dotenv()

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PubMed/PMC drug QT-context pipeline")
    p.add_argument("--drug", type=str, help="单个药物名称")
    p.add_argument("--input-xlsx", type=str, help="含药物名称的 Excel 文件（批量模式）")
    p.add_argument("--top-n", type=int, default=100, help="每个药物的最大 PubMed 结果数")
    p.add_argument("--window", type=int, default=500, help="上下文字符窗口大小")
    p.add_argument("--max-drugs", type=int, default=10, help="批量模式最大药物数")
    p.add_argument("--no-dedupe", action="store_true", help="不去重，处理每一行")
    p.add_argument("--out-dir", type=str, default="outputs", help="输出目录")
    return p.parse_args()


def get_drug_list(args: argparse.Namespace) -> list[str]:
    drugs: list[str] = []
    if args.drug:
        drugs.append(args.drug.strip())
    if args.input_xlsx:
        from src.excel_input import build_drug_jobs, resolve_sheet
        _, df = resolve_sheet(args.input_xlsx, None)
        jobs = build_drug_jobs(df, max_drugs=None, dedupe_by_name=False)
        for j in jobs:
            drugs.extend(j.get("drug_names", [j.get("drug_name", "")]))
    if not drugs:
        logger.error("未提供药物名称。请使用 --drug 或 --input-xlsx。")
        sys.exit(1)
    if not args.no_dedupe:
        seen = set()
        unique: list[str] = []
        for d in drugs:
            if d not in seen:
                seen.add(d)
                unique.append(d)
        drugs = unique
    if len(drugs) > args.max_drugs:
        logger.warning(f"截断药物列表至 {args.max_drugs} 个")
        drugs = drugs[:args.max_drugs]
    return drugs


def _load_keywords() -> list[str]:
    from src.qt_vocabulary import classifier_qt_terms_ordered
    return classifier_qt_terms_ordered()


async def run_pipeline_for_drug(
    client: PubMedMCPClient,
    drug: str,
    top_n: int,
    window: int,
    keywords: list[str],
    chembl_enrichment: dict | None = None,
) -> dict:
    extra_name_variants = None
    known_pubmed_ids: list[str] = []
    chembl_id = None

    if chembl_enrichment:
        extra_name_variants = chembl_enrichment.get("name_variants")
        known_pubmed_ids = chembl_enrichment.get("known_pubmed_ids") or []
        chembl_id = chembl_enrichment.get("chembl_id")

    # 1. 搜索
    pmids: list[str] = []
    seen_pmids: set[str] = set()

    query = build_simple_query(drug)
    search_result = await client.search_articles(query=query, max_results=top_n)
    pmids = search_result.get("pmids", [])
    seen_pmids.update(pmids)

    # Variant queries (ChEMBL name variants)
    if extra_name_variants:
        variant_queries = build_variant_queries(drug, extra_name_variants)
        for vq, vlabel in variant_queries:
            if vq == query:
                continue
            logger.info(f"Variant search: {vlabel}")
            v_result = await client.search_articles(query=vq, max_results=top_n)
            v_pmids = v_result.get("pmids", [])
            for p in v_pmids:
                if p not in seen_pmids:
                    seen_pmids.add(p)
                    pmids.append(p)

    if not pmids:
        logger.warning(f"未找到 {drug} 的 PMIDs，尝试 fallback")
        for fb_query, fb_label in iter_simple_fallbacks(drug):
            logger.info(f"尝试 fallback: {fb_label}")
            search_result = await client.search_articles(query=fb_query, max_results=top_n)
            pmids = search_result.get("pmids", [])
            if pmids:
                break

    if not pmids and not known_pubmed_ids:
        result = {"drug_name": drug, "articles": [], "contexts": []}
        if chembl_enrichment:
            result["chembl_enrichment"] = chembl_enrichment
        return result

    # 2. Fetch known PubMed IDs from ChEMBL first
    known_articles: list[dict] = []
    if known_pubmed_ids:
        logger.info(f"Fetching {len(known_pubmed_ids)} known PubMed IDs from ChEMBL")
        try:
            fetch_known = await client.fetch_articles(pmids=known_pubmed_ids)
            known_raw = fetch_known.get("articles", [])
            for a in known_raw:
                if isinstance(a, dict):
                    a["source"] = "chembl_known"
            known_articles.extend(known_raw)
        except Exception as e:
            logger.warning(f"Failed to fetch known PubMed IDs: {e}")

    # 3. 获取搜索结果文章元数据
    raw_articles: list = []
    if pmids:
        fetch_result = await client.fetch_articles(pmids=pmids)
        raw_articles = fetch_result.get("articles", [])

    # Deduplicate: remove search-result PMIDs that overlap with known
    known_pmids_set = {str(a.get("pmid", "")) for a in known_articles if isinstance(a, dict)}
    raw_articles = [a for a in raw_articles if isinstance(a, dict) and str(a.get("pmid", "")) not in known_pmids_set]

    # 4. 分流：有 PMCID 的获取全文，没有的仅用摘要
    all_raw = known_articles + raw_articles

    with_pmcid = filter_articles_with_pmcid(all_raw)

    without_pmcid_raw = [a for a in all_raw if isinstance(a, dict)]
    without_pmcid_ids = {a.get("pmid") for a in with_pmcid}
    without_pmcid = normalize_any_article(without_pmcid_raw)
    without_pmcid = [a for a in without_pmcid if a.get("pmid") not in without_pmcid_ids]
    for a in without_pmcid:
        a["fulltext_available"] = False
        a["fulltext_error"] = "No PMCID — abstract-level evidence only."
        a["sections"] = []

    # 5. 获取全文（仅对有 PMCID 的文章）
    with_fulltext = await fetch_fulltext_for_articles(client, with_pmcid)

    # 合并两组，全文文章优先排列
    all_articles = with_fulltext + without_pmcid

    # Tag ChEMBL-known articles (normalize rebuilds dicts, so tag after merge)
    known_pmids_set = {str(p).strip() for p in known_pubmed_ids}
    for a in all_articles:
        if str(a.get("pmid", "")) in known_pmids_set:
            a["source"] = "chembl_known"

    # Sort: fulltext available > chembl_known > abstract-only
    all_articles.sort(key=lambda a: (
        -int(a.get("fulltext_available", False)),
        -1 if a.get("source") == "chembl_known" else 0,
    ))

    # 6. 提取上下文
    for art in all_articles:
        ctx = extract_keyword_contexts(art, keywords, window=window)
        art.update(ctx) if isinstance(ctx, dict) else None

    # 7. 证据标注
    for art in all_articles:
        enrich_article_evidence_metadata(art, drug, extra_name_variants=extra_name_variants)
        art["drug_in_title"] = drug_in_title_abstract(drug, art, extra_name_variants=extra_name_variants)

    result = {
        "drug_name": drug,
        "articles": filter_articles(all_articles, drug),
        "contexts": [art.get("contexts", []) for art in all_articles],
    }
    if chembl_enrichment:
        result["chembl_enrichment"] = chembl_enrichment

    return result


async def main() -> None:
    args = parse_args()
    drugs = get_drug_list(args)
    keywords = _load_keywords()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    async with PubMedMCPClient.from_env() as client:
        for drug in drugs:
            logger.info(f"处理药物: {drug}")
            result = await run_pipeline_for_drug(
                client, drug, args.top_n, args.window, keywords,
            )

            slug = strip_salt_suffix(drug).replace(" ", "_").lower()
            json_path = out_dir / f"{slug}.json"
            md_path = out_dir / f"{slug}.md"

            write_json(result, json_path)
            write_markdown_report(result, md_path)
            logger.info(f"已写入: {json_path}, {md_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(main())