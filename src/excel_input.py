"""
Load drug names from a DIQTA / spreadsheet export (.xlsx).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def resolve_excel_input_path(project_root: Path, xlsx: Path) -> Path:
    """
    Resolve ``--input-xlsx``:

    - Absolute paths are returned resolved.
    - Else try ``project_root / xlsx`` (repo-relative).
    - Else try ``project_root / input / xlsx`` (i.e. under the repo ``input/`` folder).
    """
    if xlsx.is_absolute():
        return xlsx.resolve()
    direct = (project_root / xlsx).resolve()
    if direct.is_file():
        return direct
    under = (project_root / "input" / xlsx).resolve()
    if under.is_file():
        return under
    return direct


def resolve_sheet(
    xlsx_path: str | Path,
    sheet: str | int | None,
) -> tuple[str, pd.DataFrame]:
    """Resolve 0/None to first sheet; int = index. Returns (sheet name, DataFrame)."""
    p = Path(xlsx_path)
    if not p.is_file():
        raise FileNotFoundError(f"Excel file not found: {p.resolve()}")
    xl = pd.ExcelFile(p)
    if sheet is None or sheet == 0:
        name = xl.sheet_names[0]
    elif isinstance(sheet, int):
        name = xl.sheet_names[sheet]
    else:
        name = str(sheet)
    df = pd.read_excel(p, sheet_name=name)
    return name, df


def load_drug_table(
    xlsx_path: str | Path,
    *,
    sheet: str | int | None = 0,
    name_column: str = "name",
) -> pd.DataFrame:
    _, df = resolve_sheet(xlsx_path, sheet)
    return df


def build_drug_jobs(
    df: pd.DataFrame,
    *,
    name_column: str = "name",
    max_drugs: int | None = None,
    dedupe_by_name: bool = True,
) -> list[dict[str, Any]]:
    """
    Return ordered job dicts: row_index, drug_name, and optional id columns for traceability.
    """
    if name_column not in df.columns:
        raise KeyError(
            f"Column {name_column!r} not in sheet. Available: {list(df.columns)}"
        )

    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, row in df.iterrows():
        raw = row.get(name_column)
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        name = str(raw).strip()
        if not name:
            continue
        if dedupe_by_name:
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
        rec: dict[str, Any] = {
            "row_index": int(i) if isinstance(i, (int, float)) else i,
            "drug_name": name,
        }
        for col in ("Pubchem_ID", "pubchem_id", "PUBCHEM", "id"):
            if col in row.index and not pd.isna(row[col]):
                rec["pubchem_id"] = row[col]
                break
        jobs.append(rec)
        if max_drugs is not None and len(jobs) >= max_drugs:
            break
    return jobs
