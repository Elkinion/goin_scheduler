from __future__ import annotations

import datetime as dt

import pandas as pd

from .config import TASK_FALLBACK_HOURS, TZ_LOCAL


def pack_all_resources_no_gaps(tasks: pd.DataFrame, default_start_hour: int = 9) -> pd.DataFrame:
    """For each resource, remove gaps: sort COMODIN first then by start_date, chain end-to-end from earliest start."""
    if tasks is None or len(tasks) == 0:
        return tasks

    df = tasks.copy().reset_index(drop=True)
    skill_src = df["skill_names"] if "skill_names" in df.columns else pd.Series([""] * len(df))
    comodin = skill_src.fillna("").astype(str).str.upper().str.contains(r"COMOD[IÍ]N", regex=True)

    start = pd.to_datetime(df["start_date"], errors="coerce")
    dur = pd.to_numeric(df["duration"], errors="coerce")
    dur = dur.where(dur.notna() & (dur > 0), TASK_FALLBACK_HOURS)

    df["_rid"] = df["resource_id"].fillna("").astype(str).str.strip()
    df["_start"] = start
    df["_dur"] = dur
    df["_comodin"] = comodin

    new_starts = df["start_date"].copy()

    for rid, sub in df.groupby("_rid"):
        if not rid or len(sub) <= 1:
            continue
        sub_sorted = sub.copy()
        far_future = pd.Timestamp("2999-12-31 00:00:00")
        sub_sorted["_ord_start"] = sub_sorted["_start"].fillna(far_future)
        sub_sorted = sub_sorted.sort_values(
            by=["_comodin", "_ord_start", "id"],
            ascending=[False, True, True],
        )
        anchor = sub_sorted["_start"].min()
        if pd.isna(anchor):
            today = dt.date.today()
            anchor = pd.Timestamp(dt.datetime.combine(today, dt.time(default_start_hour, 0)))
        cur = anchor
        for pos, row in sub_sorted.iterrows():
            new_starts.at[pos] = cur.strftime("%Y-%m-%d %H:%M")
            cur = cur + pd.to_timedelta(row["_dur"], unit="h")

    df["start_date"] = new_starts
    return df.drop(columns=["_rid", "_start", "_dur", "_comodin"], errors="ignore")
