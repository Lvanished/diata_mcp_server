# pubmed_pmc_drug_context_agent

面向 **DIQT / 心脏毒性** 场景的文献流水线：输入**药名**（或 Excel 批量药名）→ 通过 **[cyanheads/pubmed-mcp-server](https://github.com/cyanheads/pubmed-mcp-server)** 访问 PubMed/PMC → 可选 **`--search-strategy layered`** 分层 PubMed 检索（动机与两条分支的设计见 **§5**）→ 保留带 **PMCID** 的文献并拉取 **PMC 开放全文**（若可得）→ 在摘要与全文中做 **QT/复极化相关关键词** 窗口提取，并打上 **`evidence_type`** → 输出 **JSON + Markdown**（批量时另有汇总表）。

---

## 1. 上游 MCP 与本仓库的分工

| 层级 | 职责 |
|------|------|
| **MCP 服务端**（自建或公网 HTTP） | 实现 NCBI/PubMed/PMC 的检索与抓取，对外暴露标准 MCP **工具**（如下表）。不负责你的药名策略、QT 词表、证据分类与报表格式。 |
| **本仓库（Python）** | MCP 的**客户端**：组检索式、多策略重试、调三个工具、筛 PMCID、合并全文、**本地**关键词与 `evidence_type`、写 JSON/Markdown/批量报告。 |

使用公网地址（如 `https://pubmed.caseyjhand.com/mcp`）时，**无需**在本机克隆 `pubmed-mcp-server` 源码；仅需配置 `.env` 的 HTTP 传输与 URL。

---

## 2. 本仓库使用的 MCP 工具（完整列表）

本仓库**只调用**上游 [pubmed-mcp-server](https://github.com/cyanheads/pubmed-mcp-server) 的下列 **3 个工具**，无其他 MCP 工具。Python 侧封装在 `src/mcp_client.py` 的 `PubMedMCPClient` 中。

### 2.1 `pubmed_search_articles`

| 项目 | 说明 |
|------|------|
| **作用** | 按 PubMed 检索式搜索文献，返回 PMID 列表及命中数量等。 |
| **本地封装** | `PubMedMCPClient.search_articles(query, max_results)` |
| **调用参数（本仓库传入）** | `query`（检索字符串）、`maxResults`（对应 CLI `--top-n`）、`summaryCount`: `0`、`offset`: `0` |
| **在流水线中的位置** | `src/main.py`：`run_pipeline_for_drug` 中按 `--search-strategy` 选择检索：`default` 时用 `query_builder.iter_pubmed_query_fallbacks`；`layered` 时用 `iter_layered_pubmed_query_rounds`（多轮、多分支合并，见下文）。 |

### 2.2 `pubmed_fetch_articles`

| 项目 | 说明 |
|------|------|
| **作用** | 按 PMID 批量拉取文献**元数据**（标题、摘要、期刊、年份、PMCID 等）。 |
| **本地封装** | `PubMedMCPClient.fetch_articles(pmids)` |
| **调用参数（本仓库传入）** | `pmids`（字符串列表）、`includeMesh`: `true`、`includeGrants`: `false` |
| **在流水线中的位置** | `src/main.py`：在搜索得到 PMID 后拉取详情；后续由 `article_filter` 等规范化。 |

### 2.3 `pubmed_fetch_fulltext`

| 项目 | 说明 |
|------|------|
| **作用** | 按 **PMCID** 拉取 **PMC 开放全文**（服务端解析 JATS 等），返回带章节结构的正文。并非每篇文献都有可解析全文。 |
| **本地封装** | `PubMedMCPClient.fetch_fulltext_pmc(pmcids)`（内部工具名仍为 `pubmed_fetch_fulltext`） |
| **调用参数（本仓库传入）** | `pmcids`（如 `PMC123` 或数字，服务端会规范化）、`includeReferences`: `false` |
| **在流水线中的位置** | `src/fulltext_extractor.py`：`fetch_fulltext_for_articles` **每批最多 10 个** PMCID 调用一次，合并进每条文献并设置 `fulltext_available` / 错误信息。 |

### 2.4 通用说明

- 任意工具均通过 **MCP 会话**发起：HTTP 模式下即连接 `MCP_SERVER_URL`；STDIO 模式下由本机子进程跑上游服务。
- 底层通用入口：`PubMedMCPClient.call_tool(name, arguments)`，本项目对 PubMed 场景只使用上述三个 `name`。

---

## 3. 目录与脚本结构说明

```
pubmed_pmc_drug_context_agent/
├── README.md                 # 本说明
├── .env.example              # 环境变量模板（复制为 .env）
├── requirements.txt          # Python 依赖
├── config/
│   └── qt_keywords.yaml      # QT/复极化等关键词表（供 context_extractor 使用）
├── scripts/
│   ├── setup_pubmed_mcp.sh              # 一次性：克隆并构建上游 pubmed-mcp-server（需 Bun）
│   ├── run_example.sh                   # 示例：单药 thioridazine，--top-n 20
│   └── compare_layered_vs_fulltext.py   # 可选：对比分层检索意图 vs 导出结果中的关键词摘录，生成 CSV
├── input/                    # 建议放批量 Excel；相对路径若不在仓库根下存在，会再试 input/<文件名>
├── src/
│   ├── main.py               # CLI 入口；单药 / Excel 批量；串联整条流水线
│   ├── mcp_client.py         # MCP 客户端：STDIO 或 HTTP；封装三个 PubMed 工具
│   ├── query_builder.py      # 药名 + QT 布尔检索式；失败时的多种 fallback 检索
│   ├── article_filter.py     # 规范化文章字典；筛 PMCID 等
│   ├── fulltext_extractor.py # 分批调用 pubmed_fetch_fulltext；展平章节；fulltext_available
│   ├── context_extractor.py  # 摘要+全文中关键词窗口；matched_terms；evidence_type
│   ├── excel_input.py        # 读 xlsx；按列构建去重/不去重任务列表
│   ├── report_writer.py      # 写 JSON、单药 Markdown、批量汇总 Markdown
│   └── __init__.py
└── outputs/                  # 默认输出目录（可用 --out-dir 修改）
```

**各模块一句话职责：**

- **`main.py`**：解析参数、加载 `qt_keywords.yaml`、建立 MCP 会话、对单个或批量药名执行 `run_pipeline_for_drug`、写输出文件。
- **`mcp_client.py`**：根据 `MCP_TRANSPORT` 选择 `streamable_http_client` 或 `stdio_client`；解析工具返回 JSON；仅暴露 `search_articles` / `fetch_articles` / `fetch_fulltext_pmc`。
- **`query_builder.py`**：生成 PubMed 检索式；`default` 与 `layered` 下的 tier / fallback 见源码 `QueryRound`、`build_herg_query` / `build_qt_query`。
- **`article_filter.py`**：统一文章字段；筛选带 PMCID 的记录供全文步骤使用。
- **`fulltext_extractor.py`**：调用 MCP 全文工具并与元数据合并；**不**做 QT 关键词匹配。
- **`context_extractor.py`**：在摘要与全文段落中匹配 `qt_keywords.yaml`；产出 `matched_terms`、`contexts` 及 `evidence_type`。
- **`excel_input.py`**：从 Excel 读取药名列，支持 `--max-drugs`、`--no-dedupe`。
- **`report_writer.py`**：序列化结果到 JSON/Markdown。

**`scripts/`：**

- **`setup_pubmed_mcp.sh`**：克隆 `https://github.com/cyanheads/pubmed-mcp-server.git`，在目标目录执行 `bun install` 与 `bun run rebuild`；用于**本地 STDIO/HTTP 自建服务**，与「仅用公网 HTTP」无必然关系。
- **`run_example.sh`**：进入项目根目录执行 `python -m src.main --drug "thioridazine" --top-n 20`（Unix / Git Bash）。

---

## 4. 端到端数据流（简图）

```
药名 / Excel 药名列表
    → query_builder（检索式 + fallback）
    → MCP: pubmed_search_articles
    → MCP: pubmed_fetch_articles
    → article_filter（PMCID 等）
    → MCP: pubmed_fetch_fulltext（fulltext_extractor，按批）
    → context_extractor（qt_keywords.yaml）
    → report_writer → JSON / Markdown
```

`--search-strategy layered` 时仅在第一步替换为 `iter_layered_pubmed_query_rounds`（多轮、双分支合并 PMID），其余步骤相同。

---

## 5. 分层检索策略说明（`--search-strategy layered`）

本策略与 **`default`** 共用同一套 downstream（拉元数据、PMC 全文、`qt_keywords.yaml` 窗口与 `evidence_type`），**区别在 PubMed 检索如何组式、如何扩召回**。

### 5.1 设计动机

- **`default`**：`iter_pubmed_query_fallbacks` — **药名主题阶梯**（`mesh_major` → `mesh` → `title` → `tiab`），每层两条分支（hERG/block + QT/TdP TIAB），遇首个有命中的分支即停止（纵向 fallback）；药名默认会做**去盐**规范化后再组式。
- **`layered`**：同一阶梯与分支形状，但在每一 **tier** 内合并两条分支的 PMID；**每条子检索（`__herg` / `__qt`）的 `maxResults` 均等于 CLI `--top-n`**（不再按 tier 拆分预算）。合并后的 PMID 数达到该 tier 的 `min_hits_to_stop`（见 `query_builder.TIER_MIN_HITS_TO_STOP`）时可**提前结束**后续 tier。

适用于希望 **hERG/通道机制** 与 **QT/TdP 临床/表型** 在检索上分离、且 QT 侧希望**主要锚定在标题/摘要** 的场景。

### 5.2 每一层内的两条分支（实现于 `src/query_builder.py`）

| 分支 | 逻辑概要 | 说明 |
|------|------------|------|
| **A（hERG / IKr 轴）** | **药名主题子句**（按 tier：`MeSH Major Topic`/Pharmacological Action → MeSH Terms → `[Title]` → `[Title/Abstract]`）**且** `(hERG \| KCNH2 \| …)` **且**「阻断/抑制/通道/current」类词 | 通道与阻断词通常用裸词 OR（不限定 TIAB），与 `build_herg_query(..., tier=...)` 一致；`broad` 参数保留 API 兼容性，分层默认 `broad=False`。 |
| **B（QT / TdP 轴）** | **同一药名主题子句** **且** 一串 **仅作用于 Title/Abstract** 的 QT 延长、长 QT、TdP 等短语 | 不要求一篇文献**同时**满足 A 与 B：本 tier PMID 集合为 **A ∪ B**（客户端去重合并）。 |

每一**层（tier）**都会跑完当前层配置下的 **A + B**，再根据合并后的唯一 PMID 数决定是否进入下一层。

### 5.3 分层轮次（`QueryRound`）

源码中 `iter_layered_pubmed_query_rounds` 默认顺序为：

1. **`mesh_major`**：药名为 MeSH Major Topic 或 Pharmacological Action（最高精度）。
2. **`mesh`**：药名出现在任意 MeSH Terms 或 Pharmacological Action。
3. **`title`**：药名出现在 `[Title]`（近年或未索引文献）。
4. **`tiab`**：药名在 Title 或 Abstract（末级召回）。

每层带有 `min_hits_to_stop`（见 `query_builder.TIER_MIN_HITS_TO_STOP`，MeSH/title 多为 **3**，`tiab` 为 **1**）：若本层合并后的唯一 PMID 数 **≥ 该阈值**，则**不再执行**更靠后的 tier。轮次名称会写入结果中的 `layered_round`、`query_attempts[].round` 等字段。

### 5.4 与 `default` 的对比小结

| 维度 | `default` | `layered` |
|------|-----------|-----------|
| 主检索形状 | 纵向尝试同一阶梯上的分支（遇首个命中即停） | 每层 **2 条**检索式并集 + **多轮** tier；每条子查询 `maxResults = --top-n` |
| QT 相关短语 | `build_pubmed_query` 等为宽 TIAB OR 块（视入口而定） | 分支 B：**显式限定在 Title/Abstract** |
| hERG/通道 | 视入口（可与 QT 同一大 OR 块） | 独立分支 A，与「阻断/通道」词 AND |
| 无结果时 | 继续下一分支 / 下一 tier | 继续下一 tier，或在达到 `min_hits_to_stop` 时提前结束 |

### 5.5 启用方式与输出

命令行加 **`--search-strategy layered`**（单药与 Excel 批量均支持）。JSON 中会包含 `search_strategy`、`query_attempts`（每条含策略标签、检索式、`round`）、`layered_round` 等；Markdown 单药报告对分层会列出各分支检索式。

与检索意图和全文关键词摘录是否一致，可用 **`scripts/compare_layered_vs_fulltext.py`** 对导出 JSON 生成审计 CSV（详见 **§11.3**）。

---

## 6. 重要概念：PubMed ≠ PMC 全文

- **PubMed**：通常可得题录、摘要等；由检索与 `pubmed_fetch_articles` 覆盖。
- **PMC 开放全文**：仅一部分文献具备；依赖 PMCID 与开放获取及服务端解析能力。流水线在全文不可得时仍保留**摘要级**命中，并在结果中体现 `fulltext_available` / 错误说明。

---

## 7. Python 环境与依赖

- 建议 **Python 3.10+**。

```bash
cd pubmed_pmc_drug_context_agent
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env：邮箱、可选 API Key、MCP 传输方式与 URL
```

---

## 8. 部署上游 MCP 的三种方式（可选）

上游使用 [Bun](https://bun.sh/) 构建（`bun run rebuild`、`bun run start:stdio` / `start:http`），**不要**依赖未文档化的 `npm start` 作为主入口。

| 方式 | 说明 |
|------|------|
| **A. 辅助脚本** | `bash scripts/setup_pubmed_mcp.sh`，在仓库旁生成 `pubmed-mcp-server/` 并构建。 |
| **B. npx 包** | `npx -y @cyanheads/pubmed-mcp-server@latest`，环境变量设 `MCP_TRANSPORT_TYPE=stdio` 等（见上游 README）。 |
| **C. 公网 HTTP** | `.env` 中 `MCP_TRANSPORT=http`，`MCP_SERVER_URL=https://pubmed.caseyjhand.com/mcp`（或自建 HTTP 的 `/mcp` 地址）。适合快速验证；生产环境建议自建并配置自有 NCBI 信息。 |

---

## 9. 传输方式：STDIO 与 HTTP

| 模式 | 典型场景 | 启动上游（示例） |
|------|----------|------------------|
| **STDIO** | 本机 Cursor / Claude Desktop、本地 Python 子进程 | 在 `pubmed-mcp-server/`：`MCP_TRANSPORT_TYPE=stdio bun run start:stdio` |
| **Streamable HTTP** | 远程或多客户端共用 | `MCP_TRANSPORT_TYPE=http MCP_HTTP_PORT=3010 bun run start:http` → 默认 `http://localhost:3010/mcp` |

本仓库通过 `.env` 的 `MCP_TRANSPORT=stdio|http` 与 `MCP_SERVER_URL`（HTTP 时）与上游对齐。

---

## 10. `.env` 配置摘要

复制 `.env.example` 为 `.env`，至少关注：

- **`NCBI_EMAIL`**：会映射为上游需要的 **`NCBI_ADMIN_EMAIL`**（NCBI 建议填写，利于额度与追溯）。
- **`NCBI_API_KEY`**：可选，注册后可提高请求额度。
- **`MCP_TRANSPORT`**：`stdio`（默认）或 `http`。
- **STDIO**：`MCP_SERVER_COMMAND`、`MCP_SERVER_ARGS`、`MCP_SERVER_CWD`（上游项目根目录）。
- **HTTP**：`MCP_SERVER_URL`（完整 URL，须含路径，如 `.../mcp`）。

---

## 11. 运行命令

在项目根目录：

```bash
# 单个药名（--top-n 默认 20，即每药最多 PubMed 条数）
python -m src.main --drug "thioridazine" --top-n 20
```

### 11.1 PubMed 检索策略：`--search-strategy`

| 取值 | 说明 |
|------|------|
| **`default`**（默认） | `iter_pubmed_query_fallbacks`：**mesh_major → mesh → title → tiab**，每层先试 **hERG** 分支再 **QT** 分支；遇首个返回 PMID 的分支即停止纵向尝试（药名默认去盐后再组式）。 |
| **`layered`** | **分层检索**：同上阶梯与分支，但每层 **合并** 两条分支的 PMID；**每条子检索 `maxResults` = `--top-n`**；合并 PMID 数 ≥ 该 tier 的 `min_hits_to_stop` 时可提前结束后续 tier。合并后的 PMID 列表仍按每药 **`top-n`** 截断后再 fetch。有 PMCID 时仍会拉 **PMC 开放全文**（与 `default` 一致）。 |

启用分层示例：

```bash
python -m src.main --drug "dofetilide" --search-strategy layered --top-n 20
python -m src.main --input-xlsx "input/DIQTA处理后数据.xlsx" --search-strategy layered
```

结果 JSON 中会多出 `search_strategy`、`layered_round`、`query_attempts`（含各分支检索式与 `round` 名称）等字段，Markdown 报告对 `layered` 会列出各分支。

### 11.2 从 Excel / `input/` 批量

**从 DIQTA Excel 批量**（默认读 `name` 列、首张表、药名去重）：

```bash
python -m src.main --input-xlsx "input/DIQTA处理后数据.xlsx" --top-n 20
```

- 若给出的相对路径在**仓库根下**不存在，会再尝试 **`input/<同一相对路径>`**（便于只写文件名：`--input-xlsx DIQTA处理后数据.xlsx`）。
- **`--max-drugs`**：**默认 `10`**（去重后最多处理多少个药名）。**`--max-drugs 0`** 表示不限制、跑完全表。

常用参数：

- `--name-column name`：药名所在列（默认 `name`）。
- `--sheet 0` 或 `--sheet SheetName`：工作表。
- `--no-dedupe`：按行处理，同名可出现多次。
- `--out-dir`：输出目录（默认 `outputs/`）。

批量输出示例：`outputs/<xlsx 文件名 stem>_batch_results.json`、`_batch_report.md`。

单药输出示例：`outputs/<药名规范化>_results.json`、`_report.md`。

示例脚本（需 Bash）：

```bash
bash scripts/run_example.sh
```

### 11.3 分层检索 vs 关键词摘录：对比报告（可选）

流水线导出的 JSON **不含原始全文 `sections`**（写入前已去掉）。脚本 `scripts/compare_layered_vs_fulltext.py` 用 **标题 + 摘要 + 各条 `contexts[].context`** 近似「管线实际用上的文本」，与 **分层检索在 PubMed 侧的两条分支意图**（hERG+阻断轴、QT-in-TA 轴 + 药名是否在 TA）做对照，生成 **CSV** 便于抽查。

```bash
# 默认在同级目录写出 <json 主名>_layered_audit.csv
python scripts/compare_layered_vs_fulltext.py outputs/某_batch_results.json

# 指定输出路径
python scripts/compare_layered_vs_fulltext.py outputs/某_batch_results.json -o outputs/my_audit.csv
```

**`correspondence` 列含义概要：**

- **`ok_intent_and_context`**：宽泛意图与「至少有一条 keyword context」一致。
- **`gap_no_keyword_context`**：意图上像能命中，但导出结果里没有 `contexts`（词表/窗口与检索式不一致、或证据只在未进入 context 的正文段落等）。
- **`gap_context_not_matching_layered_or`**：管线有关键词命中，但脚本用规则**重构不出**分层 OR（例如 Excel 药名为盐型全名、题录只用 base name，导致 `drug_in_title_abstract` 为假）。
- **`weak_no_intent_no_context`**：两侧都弱，建议人工看该 PMID。

若未使用 `layered` 跑出的 JSON，脚本会 **stderr 警告**，仍会计算一遍供参考。

---

## 12. `evidence_type`（每条 context）

用于粗分类 DIQT/心脏毒性相关表述：

- **`clinical_or_direct_qt_evidence`**：如 QT prolongation、long QT、torsades / TdP、配置项中的 **QT** 等。
- **`mechanistic_herg_ikr_evidence`**：hERG、KCNH2、IKr。
- **`phenotypic_repolarization_evidence`**：APD、FPD、repolarization、action/field potential duration 等。
- **`uncertain_relevance`**：命中配置词但未归入以上类别者。

---

## 13. JSON 结果结构（概要）

**单药**（`--drug`）：根对象含 `drug_name`、`search_strategy`（`default` | `layered`）、`query`、`query_strategy`、`query_attempts`（`layered` 时含各分支与 `round` 名）、`layered_round`（仅 **`layered`** 时为最后一轮 tier 名称，如 `strict`；`default` 时多为 `null`）、`effective_query`、`search_total_found`、`summary`、可选 `note`，以及 `articles[]`。每篇文章至少包括：`pmid`、`pmcid`（无则空串）、`title`、`abstract`、`journal`、`year`、`fulltext_available`、`matched_terms`、`contexts`（含 `source`、`section`、`matched_term`、`context`、`evidence_type`）。

**Excel 批量**（`--input-xlsx`）：根对象额外包含元数据，例如 `source_file`、`sheet`、`name_column`、`search_strategy`、`row_count`、`drugs_run`、`top_n`、`context_window`、`dedupe_by_name`，以及 **`results`** 数组。`results` 中每一项通常含任务信息（如 `row_index`、`drug_name`）、`ok`（是否成功）、`error`（失败时的错误信息）、`result`（成功时为与单药结构相同的完整结果对象，否则 `null`）。

---

## 14. 常见问题

- **Windows STDIO 报找不到文件（WinError 2）**：多为未安装 **Bun** 或未在 PATH 中，或 `MCP_SERVER_CWD` 路径错误。可改用 **`MCP_TRANSPORT=http`** 与 `MCP_SERVER_URL`，或改用 `MCP_SERVER_COMMAND=npx` + `MCP_SERVER_ARGS=-y,@cyanheads/pubmed-mcp-server@latest`（需 Node）。
- **HTTP 4xx/5xx**：确认 URL 含 **`/mcp`**、防火墙与 TLS；可用公网测试 URL 对比是否为本地服务问题。
- **全文为空或 `fulltext_available: false`**：常见于此文无开放全文或 JATS 段落为空；流水线仍保留摘要侧 `contexts`。
- **检索无结果**：`default` 下首条检索较严，会自动换策略（盐型后缀、`[Text Word]`、缩短 QT OR 块等）；查看 JSON 中 `query_attempts`、`query_strategy`。**`layered`** 下可看多轮 `query_attempts` 与 `layered_round`。
- **额度**：配置 `NCBI_API_KEY` 与邮箱；遵守 [NCBI 使用政策](https://www.ncbi.nlm.nih.gov/account/settings/)。

---

## 15. 许可

本目录中的 Python 客户端与工具代码为薄封装与业务流水线；上游 **pubmed-mcp-server** 为 **Apache-2.0**。使用与再发布文献内容时请遵守 NCBI 与出版商条款。
