from __future__ import annotations

from typing import Iterable

import pandas as pd

from .config import ALL_SENTINEL


def _norm(x) -> str:
    if x is None:
        return ""
    return str(x).strip()


def apply_gantt_filters(
    tasks: pd.DataFrame | None,
    resource: str | None = None,
    countries: Iterable[str] | None = None,
    objective: str | None = None,
    business_unit: str | None = None,
    date_from=None,
    date_to=None,
) -> pd.DataFrame:
    if tasks is None or len(tasks) == 0:
        return tasks if tasks is not None else pd.DataFrame()

    df = tasks.copy()
    for col in ("resource_id", "pais", "objetivo", "business_unit"):
        if col not in df.columns:
            df[col] = ""
    df["resource_id"] = df["resource_id"].fillna("").astype(str).str.strip()
    df["pais"] = df["pais"].fillna("").astype(str).str.strip().str.upper()
    df["objetivo"] = df["objetivo"].fillna("").astype(str).str.strip()
    df["business_unit"] = df["business_unit"].fillna("").astype(str).str.strip()

    if resource and resource != ALL_SENTINEL:
        df = df[df["resource_id"] == _norm(resource)]

    if countries:
        clist = [_norm(c).upper() for c in countries if _norm(c)]
        if clist and ALL_SENTINEL not in clist:
            df = df[df["pais"].isin(clist)]

    if objective and objective != ALL_SENTINEL:
        df = df[df["objetivo"] == _norm(objective)]

    if business_unit and business_unit != ALL_SENTINEL:
        df = df[df["business_unit"] == _norm(business_unit)]

    def _to_ts(x):
        if x is None:
            return None
        try:
            ts = pd.Timestamp(x)
        except (TypeError, ValueError):
            return None
        if pd.isna(ts):
            return None
        return ts.tz_localize(None) if ts.tzinfo is not None else ts

    ts_from = _to_ts(date_from)
    ts_to = _to_ts(date_to)
    if ts_from is not None or ts_to is not None:
        start_col = "start_date" if "start_date" in df.columns else ("datetime" if "datetime" in df.columns else None)
        if start_col is not None:
            starts = pd.to_datetime(df[start_col], errors="coerce")
            if getattr(starts.dtype, "tz", None) is not None:
                starts = starts.dt.tz_localize(None)
            mask = starts.notna()
            if ts_from is not None:
                mask &= starts >= ts_from
            if ts_to is not None:
                mask &= starts <= (ts_to + pd.Timedelta(days=1))
            df = df[mask]

    return df
