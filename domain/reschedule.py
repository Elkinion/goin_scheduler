from __future__ import annotations

import datetime as dt

import pandas as pd

from .config import TASK_FALLBACK_HOURS


def reschedule_resource_chain(
    gantt_df: pd.DataFrame,
    resource_id: str,
    moved_task_id: str,
    anchor_start,
) -> pd.DataFrame:
    """Chain tasks for one resource end-to-end starting at anchor_start; moved task goes first."""
    df = gantt_df.copy()
    mask = df["resource_id"].astype(str) == str(resource_id)
    idx = df.index[mask].tolist()
    if not idx:
        return df

    sub_start = pd.to_datetime(df.loc[idx, "start_date"], errors="coerce")
    order = sorted(idx, key=lambda i: (sub_start.loc[i] if pd.notna(sub_start.loc[i]) else pd.Timestamp.max, str(df.at[i, "id"])))

    moved_id = str(moved_task_id)
    moved_positions = [i for i in idx if str(df.at[i, "id"]) == moved_id]
    if moved_positions:
        first = moved_positions[0]
        order = [first] + [i for i in order if i != first]

    cur = pd.to_datetime(anchor_start, errors="coerce")
    if pd.isna(cur):
        cur = sub_start.min()
        if pd.isna(cur):
            today = dt.date.today()
            cur = pd.Timestamp(dt.datetime.combine(today, dt.time(9, 0)))

    for i in order:
        df.at[i, "start_date"] = cur.strftime("%Y-%m-%d %H:%M")
        dur = pd.to_numeric(df.at[i, "duration"], errors="coerce")
        if pd.isna(dur) or dur <= 0:
            dur = TASK_FALLBACK_HOURS
        cur = cur + pd.to_timedelta(dur, unit="h")

    return df
