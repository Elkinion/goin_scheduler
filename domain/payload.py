from __future__ import annotations

from typing import Iterable

import pandas as pd

from .config import API_TZ, TASK_FALLBACK_HOURS, TZ_LOCAL
from .country import extract_country_series

BUSINESS_UNITS = (
    "Migra Pre2Pos",
    "Base Development",
    "Refresh Key Visual",
    "Network / Coverage",
    "FMC Gross",
    "Gross",
    "Reloads",
    "Gross (low penetration)",
    "Devices",
)


def _safe_str(x) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except (TypeError, ValueError):
        pass
    return str(x)


def detect_business_unit_single(skill_names: str | None) -> str:
    s = _safe_str(skill_names).strip()
    if not s:
        return ""
    tags = [t.strip() for t in s.split("|")]
    for unit in BUSINESS_UNITS:
        if unit in tags:
            return unit
    return ""


def detect_business_unit(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).map(detect_business_unit_single)


def parse_plan_dt(series: pd.Series) -> pd.Series:
    """Parse datetime strings (Y-m-d H:M[:S]) in TZ_LOCAL. Returns tz-aware."""
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    try:
        parsed = parsed.dt.tz_localize(TZ_LOCAL, nonexistent="shift_forward", ambiguous="NaT")
    except (TypeError, AttributeError):
        parsed = pd.to_datetime(series, errors="coerce").dt.tz_localize(TZ_LOCAL)
    return parsed


def fmt_gantt(x) -> str | None:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    ts = pd.to_datetime(x, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d %H:%M")


def build_planned_from_a_plan(a_plan: pd.DataFrame | None) -> dict:
    if a_plan is None or len(a_plan) == 0:
        return {"tasks": pd.DataFrame(), "resources": pd.DataFrame(columns=["resource_id", "resource_name"])}

    ap = a_plan.copy()
    required = [
        "id", "title", "status", "priority", "description",
        "planned_start", "planned_end",
        "collab_email_plan", "skill_main", "typeTask_name", "tag", "project_name",
    ]
    for col in required:
        if col not in ap.columns:
            ap[col] = None
    if "skill_names" not in ap.columns:
        ap["skill_names"] = ""

    ap["id"] = ap["id"].astype(str)
    ap["resource_id"] = ap["collab_email_plan"].where(
        ap["collab_email_plan"].notna() & (ap["collab_email_plan"].astype(str).str.strip() != ""),
        "SIN_ASIGNAR",
    ).astype(str).str.strip()

    resources = (
        ap[["resource_id"]]
        .drop_duplicates()
        .assign(resource_name=lambda d: d["resource_id"])
        .sort_values("resource_name")
        .reset_index(drop=True)
    )

    ps = pd.to_datetime(ap["planned_start"], errors="coerce")
    pe = pd.to_datetime(ap["planned_end"], errors="coerce")
    dur_hours = (pe - ps).dt.total_seconds() / 3600.0
    dur_hours = dur_hours.where(dur_hours.notna() & (dur_hours > 0), TASK_FALLBACK_HOURS)

    tasks = pd.DataFrame({
        "id": ap["id"],
        "text": ap["title"].map(_safe_str),
        "start_date": ps.dt.strftime("%Y-%m-%d %H:%M"),
        "duration": dur_hours.astype(float),
        "resource_id": ap["resource_id"],
        "skill_main": ap["skill_main"].map(_safe_str),
        "typeTask_name": ap["typeTask_name"].map(_safe_str),
        "tag": ap["tag"].map(_safe_str),
        "project_name": ap["project_name"].map(_safe_str),
        "skill_names": ap["skill_names"].map(_safe_str),
        "status": ap["status"].map(_safe_str),
        "priority": ap["priority"].map(_safe_str),
        "description": ap["description"].map(_safe_str),
    })
    tasks["objetivo"] = detect_business_unit(tasks["skill_names"])
    tasks["business_unit"] = tasks["project_name"]
    tasks["pais"] = extract_country_series(tasks["tag"])
    tasks = tasks.merge(resources, on="resource_id", how="left")
    tasks["resource_name"] = tasks["resource_name"].fillna(tasks["resource_id"])
    tasks = tasks[tasks["start_date"].notna() & (tasks["start_date"] != "")]
    tasks = tasks.sort_values(["resource_id", "start_date", "id"]).reset_index(drop=True)

    ordered = [
        "id", "text", "start_date", "duration", "resource_id", "resource_name",
        "skill_main", "typeTask_name", "tag", "project_name", "objetivo",
        "skill_names", "business_unit", "pais", "status", "priority", "description",
    ]
    tasks = tasks[[c for c in ordered if c in tasks.columns]]
    return {"tasks": tasks, "resources": resources}


def to_utc_iso(x) -> str | None:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    ts = pd.to_datetime(x, errors="coerce")
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize(TZ_LOCAL)
    return ts.tz_convert(API_TZ).strftime("%Y-%m-%d %H:%M:%S")


def build_push_payload_editable(tasks: pd.DataFrame) -> pd.DataFrame:
    if tasks is None or len(tasks) == 0:
        return pd.DataFrame(columns=["id", "datetime", "deadline"])

    start = pd.to_datetime(tasks["start_date"], errors="coerce")
    try:
        start = start.dt.tz_localize(TZ_LOCAL, nonexistent="shift_forward", ambiguous="NaT")
    except TypeError:
        pass
    dur = pd.to_numeric(tasks["duration"], errors="coerce")
    dur = dur.where(dur.notna() & (dur > 0), TASK_FALLBACK_HOURS)
    end = start + pd.to_timedelta(dur, unit="h")

    df = pd.DataFrame({
        "id": tasks["id"].astype(str),
        "datetime": start.map(to_utc_iso),
        "deadline": end.map(to_utc_iso),
    })
    df = df[df["datetime"].notna() & df["deadline"].notna()]
    return df.reset_index(drop=True)


def build_push_payload_original(
    a_plan: pd.DataFrame,
    baseline: pd.DataFrame | None,
) -> pd.DataFrame:
    if a_plan is None or len(a_plan) == 0:
        return pd.DataFrame(columns=["id", "datetime", "deadline"])

    payload = pd.DataFrame({
        "id": a_plan["id"].astype(str),
        "datetime": a_plan["datetime"].map(to_utc_iso),
        "deadline": a_plan["deadline"].map(to_utc_iso),
    })
    payload = payload[payload["datetime"].notna() & payload["deadline"].notna()]
    if baseline is None or len(baseline) == 0:
        return payload.reset_index(drop=True)

    base = pd.DataFrame({
        "id": baseline["id"].astype(str),
        "base_datetime": baseline["datetime"].map(to_utc_iso),
        "base_deadline": baseline["deadline"].map(to_utc_iso),
    })
    merged = payload.merge(base, on="id", how="left")
    keep = (
        merged["base_datetime"].isna()
        | merged["base_deadline"].isna()
        | (merged["datetime"] != merged["base_datetime"])
        | (merged["deadline"] != merged["base_deadline"])
    )
    return merged.loc[keep, ["id", "datetime", "deadline"]].reset_index(drop=True)
