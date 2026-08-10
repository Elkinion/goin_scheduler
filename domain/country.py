from __future__ import annotations

import re
from typing import Iterable

import pandas as pd

COUNTRY_CATALOG = {
    "BO": "Bolivia",
    "CA": "Centroamérica",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "GT": "Guatemala",
    "HN": "Honduras",
    "NI": "Nicaragua",
    "PA": "Panamá",
    "PY": "Paraguay",
    "SV": "El Salvador",
}

ISO2_LIST = (
    "CO", "PA", "CR", "HN", "NI", "PY", "BO", "SV", "GT",
    "EC", "PE", "CL", "AR", "UY", "BR", "MX", "DO", "CA",
)
_ISO_SET = set(ISO2_LIST)
_ISO_PATTERN = re.compile(
    rf"(?:^|[^A-Z])({'|'.join(ISO2_LIST)})(?:[^A-Z]|$)"
)


def extract_country_from_tag(tag) -> str:
    if tag is None:
        return ""
    s = str(tag).strip().upper()
    if not s or s == "NAN":
        return ""
    if s in _ISO_SET:
        return s
    m = _ISO_PATTERN.search(s)
    return m.group(1) if m else ""


def extract_country_series(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).map(extract_country_from_tag)


def _codes_from_col(df: pd.DataFrame | None, col: str) -> list[str]:
    if df is None or len(df) == 0 or col not in df.columns:
        return []
    s = df[col].fillna("").astype(str).str.strip().str.upper()
    return [x for x in s.tolist() if x]


def build_country_choices(
    planned_tasks: pd.DataFrame | None,
    comments: pd.DataFrame | None,
) -> dict[str, str]:
    """Returns dict mapping label -> code (matches Shiny setNames behavior)."""
    planned = _codes_from_col(planned_tasks, "pais")
    comment_codes = _codes_from_col(comments, "country")
    all_codes = sorted(set(list(COUNTRY_CATALOG.keys()) + planned + comment_codes))
    out: dict[str, str] = {}
    for code in all_codes:
        label = f"{code} · {COUNTRY_CATALOG[code]}" if code in COUNTRY_CATALOG else code
        out[label] = code
    return out
