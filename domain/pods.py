from __future__ import annotations

import re

import pandas as pd


def _norm(x) -> str:
    if x is None:
        return ""
    return re.sub(r"\s+", " ", str(x).lower().strip())


def build_email_to_pod(a_plan: pd.DataFrame | None, workers_pods: pd.DataFrame | None) -> dict[str, str]:
    if a_plan is None or a_plan.empty or workers_pods is None or workers_pods.empty:
        return {}
    needed = {"collab_email", "collab_first_name", "collab_last_name"}
    if not needed.issubset(set(a_plan.columns)):
        return {}
    wp = workers_pods.copy()
    if not {"first_name", "last_name", "pod"}.issubset(set(wp.columns)):
        return {}
    wp["_fn"] = wp["first_name"].map(_norm)
    wp["_ln"] = wp["last_name"].map(_norm)
    wp["_full"] = (wp["_fn"] + " " + wp["_ln"]).str.strip()
    by_full = dict(zip(wp["_full"], wp["pod"].astype(str)))

    ap = a_plan[["collab_email", "collab_first_name", "collab_last_name"]].copy()
    ap["_email"] = ap["collab_email"].map(_norm)
    ap["_full"] = (ap["collab_first_name"].map(_norm) + " " + ap["collab_last_name"].map(_norm)).str.strip()
    ap = ap[ap["_email"] != ""]
    mapping: dict[str, str] = {}
    for _, row in ap.iterrows():
        pod = by_full.get(row["_full"])
        if pod:
            mapping[row["_email"]] = pod
    return mapping


def attach_pod_to_planned(planned_tasks: pd.DataFrame, email_to_pod: dict[str, str]) -> pd.DataFrame:
    if planned_tasks is None or planned_tasks.empty:
        return planned_tasks
    df = planned_tasks.copy()
    df["pod"] = df["resource_id"].map(lambda e: email_to_pod.get(_norm(e), "Sin pod"))
    return df
