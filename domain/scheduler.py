from __future__ import annotations

import datetime as dt
import re
from math import isclose

import pandas as pd

from .config import API_TZ, COUNTRY_CODES, DAY_START_HOUR, WORK_ROLES

CNT_COLS = tuple(f"cnt_{c}" for c in COUNTRY_CODES)


def infer_skill_main(skill_names) -> str:
    s = "" if skill_names is None else str(skill_names)
    if not s.strip():
        return ""
    x = s.upper()
    if re.search(r"ADAPTACIONES|ADAP", x):
        return "ADAPTACIONES"
    if re.search(r"\bPRO\b", x):
        return "PRO"
    if re.search(r"\bPLUS\b", x):
        return "PLUS"
    if re.search(r"EST[ÁA]NDAR|\bESTANDAR\b", x):
        return "ESTÁNDAR"
    return ""


def normalize_type_task_for_sla(type_task_name, skill_main) -> str:
    if skill_main and skill_main == "ADAPTACIONES":
        return "ADAP"
    s = "" if type_task_name is None else str(type_task_name).strip()
    if not s:
        return ""
    x = s.lower()
    if re.search(r"est[aá]tic", x):
        return "Key Visual Estático"
    if re.search(r"animad", x):
        return "Key Visual Animado"
    return re.sub(r"\s+", " ", s).strip()


def tag_to_country(tag) -> str:
    if tag is None:
        return ""
    t = str(tag).strip().upper()
    return t if t in COUNTRY_CODES else ""


def _is_micro(x) -> bool:
    if x is None or pd.isna(x):
        return False
    return isclose(x, 1/30, abs_tol=1e-9) or isclose(x, 1/15, abs_tol=1e-9)


def _micro_kind(x) -> str:
    if x is None or pd.isna(x):
        return ""
    if isclose(x, 1/30, abs_tol=1e-9):
        return "micro_30"
    if isclose(x, 1/15, abs_tol=1e-9):
        return "micro_15"
    return ""


def _micro_cap(kind: str) -> int:
    return 30 if kind == "micro_30" else 15


def _day_start(d) -> pd.Timestamp:
    if isinstance(d, pd.Timestamp):
        d = d.date()
    return pd.Timestamp(dt.datetime.combine(d, dt.time(DAY_START_HOUR, 0)), tz=API_TZ)


def _norm(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip().lower()


def add_sla_to_a(a: pd.DataFrame, df_sla: pd.DataFrame) -> pd.DataFrame:
    needed = {"collab_userPosition_name", "typeTask_name", "skill_main", "tiempo_dias", "cantidad_por_dia"}
    missing = needed - set(df_sla.columns)
    if missing:
        raise ValueError(f"df_sla NO tiene columnas: {sorted(missing)}")

    df = a.copy()
    if "skill_main" not in df.columns:
        df["skill_main"] = ""
    if "typeTask_sla" not in df.columns:
        df["typeTask_sla"] = ""
    if "tiempo_estimado_dias" not in df.columns:
        df["tiempo_estimado_dias"] = pd.NA

    def _blank(x):
        if x is None:
            return True
        try:
            if pd.isna(x):
                return True
        except (TypeError, ValueError):
            pass
        return str(x).strip() == ""

    df["skill_main"] = [
        infer_skill_main(sk) if _blank(sm) else str(sm)
        for sm, sk in zip(df["skill_main"], df.get("skill_names", pd.Series([""] * len(df))))
    ]
    df["typeTask_sla"] = [
        normalize_type_task_for_sla(tt, sm) if _blank(ts) else str(ts)
        for ts, tt, sm in zip(df["typeTask_sla"], df.get("typeTask_name", pd.Series([""] * len(df))), df["skill_main"])
    ]

    df["_k_role"] = df["collab_userPosition_name"].map(_norm)
    df["_k_type"] = df["typeTask_sla"].map(_norm)
    df["_k_skill"] = df["skill_main"].map(_norm)

    sla = df_sla.copy()
    sla["_k_role"] = sla["collab_userPosition_name"].map(_norm)
    sla["_k_type"] = sla["typeTask_name"].map(_norm)
    sla["_k_skill"] = sla["skill_main"].map(_norm)
    sla = sla[["_k_role", "_k_type", "_k_skill", "tiempo_dias", "cantidad_por_dia"]]

    out = df.merge(sla, on=["_k_role", "_k_type", "_k_skill"], how="left")

    deliverable = pd.to_numeric(out.get("deliverable", 0), errors="coerce").fillna(0)
    cxd = pd.to_numeric(out["cantidad_por_dia"], errors="coerce")

    def _calc(row_cxd, row_deliv, row_tiempo_dias, existing):
        if pd.notna(existing):
            return existing
        if pd.notna(row_cxd):
            if row_cxd == 30:
                return (1 / 30) * row_deliv
            if row_cxd == 15:
                return (1 / 15) * row_deliv
            if row_cxd == 1:
                return 1.0
        return row_tiempo_dias

    out["tiempo_estimado_dias"] = [
        _calc(c, d, t, e)
        for c, d, t, e in zip(cxd, deliverable, out["tiempo_dias"], out["tiempo_estimado_dias"])
    ]

    return out.drop(columns=["_k_role", "_k_type", "_k_skill"])


def init_schedule(a: pd.DataFrame, workers_allowed: pd.DataFrame, anchor_date) -> pd.DataFrame:
    from_a = a[
        a["collab_email"].notna()
        & (a["collab_email"].astype(str).str.strip() != "")
        & a["collab_userPosition_name"].isin(WORK_ROLES)
    ][["collab_email", "collab_userPosition_name"]].drop_duplicates()

    all_workers = workers_allowed[["collab_email"]].drop_duplicates().copy()
    all_workers["collab_userPosition_name"] = pd.NA

    sched = pd.concat([from_a, all_workers], ignore_index=True).drop_duplicates(subset=["collab_email"]).reset_index(drop=True)
    sched["next_free"] = _day_start(anchor_date)
    for c in CNT_COLS:
        sched[c] = 0
    sched["cnt_total"] = 0
    return sched


def _pick_worker(sched: pd.DataFrame, workers_allowed: pd.DataFrame, cc: str, preferred_email: str = "") -> pd.Series | None:
    if preferred_email and preferred_email in set(sched["collab_email"]):
        row = sched[sched["collab_email"] == preferred_email].iloc[0]
        return row

    cand = sched
    if cc:
        allowed = set(workers_allowed[workers_allowed["allowed_code"] == cc]["collab_email"])
        cand = cand[cand["collab_email"].isin(allowed)]
        if cand.empty:
            return None

    coln = f"cnt_{cc}" if cc else None
    pen_country = cand[coln] if coln and coln in cand.columns else pd.Series([0] * len(cand), index=cand.index)

    def _to_epoch(x):
        try:
            ts = pd.Timestamp(x)
            if pd.isna(ts):
                return 0.0
            return ts.timestamp()
        except Exception:
            return 0.0

    epoch = cand["next_free"].map(_to_epoch).astype(float)
    pen_country = pd.to_numeric(pen_country, errors="coerce").fillna(0).astype(float)
    cnt_total = pd.to_numeric(cand["cnt_total"], errors="coerce").fillna(0).astype(float)
    score = epoch + pen_country * 1_000_000.0 + cnt_total * 10_000.0
    idx = score.sort_values(kind="mergesort").index[0]
    return cand.loc[idx]


def _bump(sched: pd.DataFrame, email: str, cc: str, add_total: int = 1, add_cc: int = 1) -> pd.DataFrame:
    mask = sched["collab_email"] == email
    sched.loc[mask, "cnt_total"] = sched.loc[mask, "cnt_total"] + add_total
    if cc:
        coln = f"cnt_{cc}"
        if coln in sched.columns:
            sched.loc[mask, coln] = sched.loc[mask, coln] + add_cc
    return sched


def _parse_arrival(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return pd.Timestamp("2100-01-01", tz=API_TZ)
    ts = pd.to_datetime(x, errors="coerce", utc=True)
    if pd.isna(ts):
        return pd.Timestamp("2100-01-01", tz=API_TZ)
    return ts.tz_convert(API_TZ) if ts.tzinfo else ts.tz_localize(API_TZ)


def schedule_tasks_from_today(a: pd.DataFrame, workers_allowed: pd.DataFrame, anchor_date=None) -> dict:
    if anchor_date is None:
        anchor_date = dt.date.today()
    sched = init_schedule(a, workers_allowed, anchor_date)
    assigned: list[dict] = []

    todo = a.copy()
    todo["arrival"] = todo["datetime"].map(_parse_arrival)
    todo["cc"] = todo["tag"].map(tag_to_country)
    todo["micro"] = todo["tiempo_estimado_dias"].map(_is_micro)
    todo["mkind"] = todo["tiempo_estimado_dias"].map(_micro_kind)
    todo["preferred_email"] = todo["collab_email"].fillna("").astype(str).str.strip()
    todo = todo.sort_values(["arrival", "id"]).reset_index(drop=True)

    normal = todo[~todo["micro"]]
    micro = todo[todo["micro"]]

    for _, trow in normal.iterrows():
        if pd.isna(trow["tiempo_estimado_dias"]):
            assigned.append({"id": trow["id"], "collab_email_new": "", "planned_start": pd.NaT, "planned_end": pd.NaT, "note": "NO_DURATION"})
            continue
        w = _pick_worker(sched, workers_allowed, trow["cc"], trow["preferred_email"])
        if w is None:
            assigned.append({"id": trow["id"], "collab_email_new": "", "planned_start": pd.NaT, "planned_end": pd.NaT, "note": "NO_WORKER_FOR_COUNTRY"})
            continue
        start = w["next_free"]
        end = start + pd.to_timedelta(trow["tiempo_estimado_dias"] * 24, unit="h")
        note = "OK_PREFERRED" if trow["preferred_email"] and trow["preferred_email"] == w["collab_email"] else "OK"
        assigned.append({"id": trow["id"], "collab_email_new": w["collab_email"], "planned_start": start, "planned_end": end, "note": note})
        sched.loc[sched["collab_email"] == w["collab_email"], "next_free"] = end
        sched = _bump(sched, w["collab_email"], trow["cc"])

    micro_valid = micro[micro["mkind"] != ""].copy()
    micro_valid["cc_grp"] = micro_valid["cc"].where(micro_valid["cc"] != "", "NO_COUNTRY")
    for (cc_grp, kind), group in micro_valid.groupby(["cc_grp", "mkind"], sort=False):
        g = group.sort_values(["arrival", "id"]).reset_index(drop=True)
        ccg = "" if cc_grp == "NO_COUNTRY" else cc_grp
        cap = _micro_cap(kind)
        each_dur = float(g["tiempo_estimado_dias"].iloc[0])
        idx = 0
        while idx < len(g):
            pref = g.at[idx, "preferred_email"]
            w = _pick_worker(sched, workers_allowed, ccg, pref)
            if w is None:
                for rest_i in range(idx, len(g)):
                    assigned.append({"id": g.at[rest_i, "id"], "collab_email_new": "", "planned_start": pd.NaT, "planned_end": pd.NaT, "note": "NO_WORKER_FOR_COUNTRY"})
                break
            base_day = w["next_free"].date() if isinstance(w["next_free"], pd.Timestamp) else pd.Timestamp(w["next_free"]).date()
            block_start = _day_start(base_day)
            if w["next_free"] > block_start:
                block_start = w["next_free"]
            take_n = min(cap, len(g) - idx)
            chunk = g.iloc[idx:idx + take_n]
            for k in range(take_n):
                s = block_start + pd.to_timedelta(k * each_dur * 24, unit="h")
                e = block_start + pd.to_timedelta((k + 1) * each_dur * 24, unit="h")
                note = f"OK_MICRO_PACK_{cap}_PREFERRED" if pref and pref == w["collab_email"] else f"OK_MICRO_PACK_{cap}"
                assigned.append({"id": chunk.iloc[k]["id"], "collab_email_new": w["collab_email"], "planned_start": s, "planned_end": e, "note": note})
            block_end = block_start + pd.to_timedelta(take_n * each_dur * 24, unit="h")
            sched.loc[sched["collab_email"] == w["collab_email"], "next_free"] = block_end
            sched = _bump(sched, w["collab_email"], ccg, add_total=take_n, add_cc=take_n)
            idx += take_n

    assigned_df = pd.DataFrame(assigned)
    assigned_df["id"] = assigned_df["id"].astype(str)

    a2 = a.copy()
    a2["id"] = a2["id"].astype(str)
    for col in ("planned_start", "planned_end", "note", "collab_email_plan", "collab_email_new"):
        if col in a2.columns:
            a2 = a2.drop(columns=[col])
    a_plan = a2.merge(assigned_df, on="id", how="left")
    a_plan["collab_email_plan"] = a_plan["collab_email_new"]
    a_plan = a_plan.drop(columns=["collab_email_new"])
    return {"a_plan": a_plan, "schedule": sched, "assignments": assigned_df}
