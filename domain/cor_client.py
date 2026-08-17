"""
ProjectCor API client.

Base URL:
    https://api.projectcor.com/v1   (constant `API_BASE` en `domain.config`)

Autenticación:
    OAuth2 Client Credentials.
    POST {API_BASE}/oauth/token
        query : grant_type=client_credentials
        header: Authorization: Basic base64(CLIENT_ID:CLIENT_SECRET)
        resp  : { "access_token": "..." }
    El token se usa como Bearer en el resto de llamadas.
    -> Implementado en `get_token()`.

Endpoints usados
----------------

1) Listar tareas por status  (paginado)
   GET {API_BASE}/tasks
       query: page, perPage, filters={"status":"<status>"}
       resp : { "data": [ {task}, ... ], "page": n, "lastPage": m }
   -> `get_tasks_by_status_paged(token, status_value, per_page=200)`

   Se descarga página a página hasta que:
     - `data` viene vacío, o
     - `len(data) < per_page`, o
     - `page >= lastPage`
     - hard-cap de 500 páginas por seguridad.

2) Detalle de tarea (para enriquecer skills y typeTask)
   GET {API_BASE}/tasks/{task_id}
       resp: { "id":..., "skill":[...], "typeTask":{"names":[{"name":"..."}]}, ... }
   -> `get_task_by_id(token, task_id)`
   -> `enrich_tasks_with_details(...)` paraleliza con ThreadPoolExecutor
      (default 32 workers) y cachea en disco en `_DETAILS_CACHE_FILE`
      (`{TEMP}/goin_scheduler_cache/task_details.json`) para no re-pegar
      la API entre sincronizaciones.

3) Actualizar fechas de una tarea
   PATCH {API_BASE}/tasks/{task_id}
       body: { "datetime": "<UTC ISO>", "deadline": "<UTC ISO>" }
       resp: 2xx OK / 4xx-5xx error
   -> `patch_task_dates(token, task_id, datetime_utc, deadline_utc)`
   -> `push_tasks_to_cor(payload_df)` itera secuencialmente (no en paralelo,
      para no gatillar rate-limits en un endpoint de escritura).

Statuses conocidos (verificados 2026-08-12 cruzando el dump completo de la API
contra el "Reporte general de tareas" descargado de la UI de Cor — 9,789 IDs
comunes, mapeo 1:1)
----------------------------------------------------------------------
    API             UI de Cor       En la app       ¿Se planifica?
    ----            ---------       ---------       --------------
    "nueva"         Nueva           "nueva"         SÍ
    "en_proceso"    En proceso      "en_proceso"    SÍ
    "en_revision"   En revisión     "en_revision"   SÍ
    "en_diseno"     Ajustes         "ajustes"       SÍ  (renombrado en ingest)
    "estancada"     Suspendida      "suspendida"    NO  (renombrado en ingest)
    "finalizada"    Finalizada      "finalizada"    NO

Divergencias importantes de nombres entre API y UI:
    - API "en_diseno"  <->  UI "Ajustes"
    - API "estancada"  <->  UI "Suspendida"
`build_a_from_cor` renombra ambos en el DataFrame de salida para que lo que ve
el usuario en el planificador coincida con lo que ve en Cor.

`STATUSES_NO_FINAL` define qué statuses (nombres crudos de la API) sí se
descargan y se meten en el cronograma. "finalizada", "estancada" y cualquier
otro NO se traen — la app nunca reserva calendario con ellos.

Notas de payload (campos que sí usamos aguas abajo)
---------------------------------------------------
De /tasks (list) usamos vía `pd.json_normalize`:
    id, title, description, project_id, project_name, deadline, status,
    priority, archived, user_id, task_father, hour_charged, order_tasks,
    datetime, deliverable,
    collaborators[]  ->  se aplana a collab_id / collab_first_name /
                          collab_last_name / collab_email / collab_email_n /
                          collab_userPosition_name (`_first_collab` +
                          `_count_emails`).

De /tasks/{id} (detalle) solo extraemos:
    skill[]     ->  concatenado en `skill_names` con `_extract_skills`
                    (elige el label preferido `preferred_lang="es"`).
    typeTask.names[0].name  ->  `typeTask_name`.

Filtros / limpiezas aplicadas antes de exponer el DataFrame:
    - Se descartan filas con `archived == True` (ver `_coerce_archived`).
    - `tag` se saca del primer `[XX]` en el título (país u origen).

Credenciales
------------
`COR_API_KEY` y `COR_CLIENT_SECRET` se resuelven en `domain/config.py`
en este orden:
    1) variables de entorno del proceso
    2) `st.secrets` de Streamlit
    3) default hardcoded (solo para desarrollo)
"""

from __future__ import annotations

import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import API_BASE, COR_API_KEY, COR_CLIENT_SECRET

_CACHE_DIR = Path(os.environ.get("TEMP", "/tmp")) / "goin_scheduler_cache"
_DETAILS_CACHE_FILE = _CACHE_DIR / "task_details.json"


def _load_details_cache() -> dict[str, dict]:
    try:
        return json.loads(_DETAILS_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_details_cache(cache: dict[str, dict]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _DETAILS_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass

STATUSES_NO_FINAL = ("en_revision", "nueva", "en_proceso", "en_diseno")
STATUSES_FINAL = ("finalizada",)


def get_token(timeout: float = 30.0) -> str:
    basic = base64.b64encode(f"{COR_API_KEY}:{COR_CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        f"{API_BASE}/oauth/token",
        params={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _safe_int(x) -> int | None:
    if x is None:
        return None
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, int):
        return x
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def get_tasks_by_status_paged(token: str, status_value: str, per_page: int = 200) -> list[dict]:
    filters_json = f'{{"status":"{status_value}"}}'
    page = 1
    rows: list[dict] = []
    while True:
        r = requests.get(
            f"{API_BASE}/tasks",
            params={"page": page, "perPage": per_page, "filters": filters_json},
            headers=_bearer_headers(token),
            timeout=60,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data") or []
        if not data:
            break
        rows.extend(data)
        if len(data) < per_page:
            break
        last_page = _safe_int(payload.get("lastPage"))
        cur_page = _safe_int(payload.get("page"))
        if last_page is not None and cur_page is not None and not cur_page < last_page:
            break
        page += 1
        if page > 500:
            break
    return rows


def get_task_by_id(token: str, task_id: str) -> dict | None:
    r = requests.get(
        f"{API_BASE}/tasks/{task_id}",
        headers=_bearer_headers(token),
        timeout=30,
    )
    if r.status_code >= 400:
        return None
    return r.json()


def _norm_name(x: Any) -> str:
    if x is None:
        return ""
    s = str(x).lower().strip()
    return re.sub(r"\s+", " ", s)


def _collab_full_name(c: dict) -> str:
    fn = str(c.get("first_name") or "").strip()
    ln = str(c.get("last_name") or "").strip()
    return re.sub(r"\s+", " ", f"{fn} {ln}").strip()


def _is_allowed(c: dict, workers_df: pd.DataFrame | None, allowed_roles: tuple[str, ...]) -> bool:
    if workers_df is None or workers_df.empty:
        return False
    if "puesto" not in workers_df.columns or "worker_name" not in workers_df.columns:
        return False
    roles = {_norm_name(r) for r in allowed_roles}
    wk = workers_df[workers_df["puesto"].map(_norm_name).isin(roles)]
    if wk.empty:
        return False
    full = _norm_name(_collab_full_name(c))
    wk_full = set(wk["worker_name"].map(_norm_name))
    if full and full in wk_full:
        return True
    fn = _norm_name(c.get("first_name") or "")
    ln = _norm_name(c.get("last_name") or "")
    if fn and ln:
        pairs = set(
            _norm_name(f"{a} {b}")
            for a, b in zip(wk.get("first_name", []), wk.get("last_name", []))
        )
        if _norm_name(f"{fn} {ln}") in pairs:
            return True
    return False


def _first_collab(
    collabs: Any,
    workers_df: pd.DataFrame | None = None,
    allowed_roles: tuple[str, ...] = ("Designer 2", "Designer 1", "Multimedia producer"),
) -> dict | None:
    if not collabs:
        return None
    if isinstance(collabs, dict):
        collabs = [collabs]
    if not isinstance(collabs, list):
        return None
    if not collabs:
        return None
    for c in collabs:
        if isinstance(c, dict) and _is_allowed(c, workers_df, allowed_roles):
            return c
    first = collabs[0]
    return first if isinstance(first, dict) else None


def _count_emails(collabs: Any) -> int:
    if not collabs:
        return 0
    if isinstance(collabs, dict):
        collabs = [collabs]
    if not isinstance(collabs, list):
        return 0
    n = 0
    for c in collabs:
        if isinstance(c, dict):
            e = str(c.get("email") or "").strip()
            if e:
                n += 1
    return n


def _extract_skills(skill_list: Any, preferred_lang: str = "es") -> str:
    if not skill_list:
        return ""
    if isinstance(skill_list, dict):
        skill_list = [skill_list]
    if not isinstance(skill_list, list) or not skill_list:
        return ""
    by_label: dict[str, dict] = {}
    for s in skill_list:
        if not isinstance(s, dict):
            continue
        label_id = str(s.get("label_id") or "")
        lang = str(s.get("lang") or "").lower()
        cur = by_label.get(label_id)
        if cur is None or (lang == preferred_lang.lower() and (cur.get("lang") or "").lower() != preferred_lang.lower()):
            by_label[label_id] = s
    names: list[str] = []
    for s in by_label.values():
        nm = str(s.get("name") or "").strip()
        if nm and nm not in names:
            names.append(nm)
    return " | ".join(names)


def _extract_type_task_name(type_task_obj: Any) -> str:
    if not isinstance(type_task_obj, dict):
        return ""
    names = type_task_obj.get("names")
    if isinstance(names, list) and names and isinstance(names[0], dict):
        return str(names[0].get("name") or "")
    return ""


def _coerce_archived(x: Any) -> bool:
    if x is None:
        return False
    if isinstance(x, bool):
        return x
    if isinstance(x, list) and x:
        x = x[0]
    s = str(x).strip().lower()
    return s in ("true", "1", "t", "yes", "y")


def enrich_tasks_with_details(
    token: str,
    task_ids: list[str],
    preferred_lang: str = "es",
    max_workers: int = 32,
    progress_cb=None,
    use_cache: bool = True,
) -> pd.DataFrame:
    unique_ids = [str(x) for x in dict.fromkeys(task_ids)]
    total = len(unique_ids)
    if total == 0:
        return pd.DataFrame(columns=["id", "skill_names", "typeTask_name"])

    cache = _load_details_cache() if use_cache else {}
    rows: list[dict] = []
    to_fetch: list[str] = []
    for tid in unique_ids:
        cached = cache.get(tid)
        if cached is not None:
            rows.append({
                "id": tid,
                "skill_names": cached.get("skill_names", ""),
                "typeTask_name": cached.get("typeTask_name", ""),
            })
        else:
            to_fetch.append(tid)

    done_cached = len(rows)
    if progress_cb and done_cached:
        progress_cb(done_cached, total)

    def _fetch_one(tid: str) -> dict:
        payload = get_task_by_id(token, tid)
        if payload is None:
            return {"id": str(tid), "skill_names": "", "typeTask_name": ""}
        return {
            "id": str(payload.get("id", tid)),
            "skill_names": _extract_skills(payload.get("skill"), preferred_lang),
            "typeTask_name": _extract_type_task_name(payload.get("typeTask")),
        }

    if to_fetch:
        done = done_cached
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch_one, tid): tid for tid in to_fetch}
            for fut in as_completed(futures):
                res = fut.result()
                rows.append(res)
                cache[res["id"]] = {
                    "skill_names": res["skill_names"],
                    "typeTask_name": res["typeTask_name"],
                }
                done += 1
                if progress_cb:
                    progress_cb(done, total)
        if use_cache:
            _save_details_cache(cache)

    return pd.DataFrame(rows)


def _rows_to_tasks_df(
    all_rows: list[dict],
    workers_df: pd.DataFrame | None,
    preferred_lang: str,
    token: str,
    progress_cb=None,
) -> pd.DataFrame:
    if not all_rows:
        return pd.DataFrame()

    tasks = pd.json_normalize(all_rows, sep="_")
    if "collaborators" not in tasks.columns:
        tasks["collaborators"] = [[] for _ in range(len(tasks))]

    first_collabs = [_first_collab(c, workers_df=workers_df) for c in tasks["collaborators"]]
    tasks["collab_id"] = [str(c.get("id")) if c and c.get("id") is not None else "" for c in first_collabs]
    tasks["collab_first_name"] = [str(c.get("first_name") or "") if c else "" for c in first_collabs]
    tasks["collab_last_name"] = [str(c.get("last_name") or "") if c else "" for c in first_collabs]
    tasks["collab_email"] = [str(c.get("email") or "") if c else "" for c in first_collabs]
    tasks["collab_email_n"] = [_count_emails(c) for c in tasks["collaborators"]]

    def _pos_name(c):
        if not c:
            return ""
        v = c.get("userPosition.name")
        if v:
            return str(v)
        up = c.get("userPosition")
        if isinstance(up, dict):
            return str(up.get("name") or "")
        return ""

    tasks["collab_userPosition_name"] = [_pos_name(c) for c in first_collabs]
    tasks = tasks.drop(columns=["collaborators"], errors="ignore")

    ids_list = tasks["id"].astype(str).tolist()
    cache_now = _load_details_cache()
    cached_hits = sum(1 for x in ids_list if x in cache_now)
    to_fetch_n = len(ids_list) - cached_hits
    if progress_cb:
        progress_cb(
            f"Enriqueciendo {len(ids_list)} tareas · cache: {cached_hits} · a traer: {to_fetch_n}",
            0,
            len(ids_list),
        )

    def _enrich_progress(done, total):
        if progress_cb:
            progress_cb(f"Enriqueciendo tareas ({done}/{total})", done, total)

    details = enrich_tasks_with_details(
        token,
        ids_list,
        preferred_lang,
        progress_cb=_enrich_progress,
    )

    project_col = "project_name" if "project_name" in tasks.columns else ("project.name" if "project.name" in tasks.columns else None)
    tasks["id"] = tasks["id"].astype(str)
    tasks["project_name"] = tasks[project_col].astype(str) if project_col else ""

    merged = tasks.merge(details, on="id", how="left")

    keep_cols = [
        "id", "title", "description", "project_id", "project_name", "deadline", "status", "priority",
        "archived", "user_id", "task_father", "hour_charged", "order_tasks", "datetime", "deliverable",
        "collab_id", "collab_first_name", "collab_last_name", "collab_email", "collab_email_n",
        "collab_userPosition_name", "skill_names", "typeTask_name",
    ]
    for c in keep_cols:
        if c not in merged.columns:
            merged[c] = ""

    def _extract_tag(title: str) -> str:
        m = re.search(r"\[(.*?)\]", str(title or ""))
        return m.group(1) if m else ""

    merged["tag"] = merged["title"].map(_extract_tag)

    out = merged[keep_cols + ["tag"]].copy()
    if "status" in out.columns:
        out["status"] = out["status"].replace({
            "en_diseno": "ajustes",
            "estancada": "suspendida",
        })
    if "archived" in out.columns:
        out["archived"] = out["archived"].map(_coerce_archived)
    return out


def build_a_from_cor(
    workers_df: pd.DataFrame | None = None,
    preferred_lang: str = "es",
    progress_cb=None,
) -> pd.DataFrame:
    token = get_token()

    all_rows: list[dict] = []
    for i, status in enumerate(STATUSES_NO_FINAL):
        if progress_cb:
            progress_cb(f"Descargando status={status}", i, len(STATUSES_NO_FINAL))
        all_rows.extend(get_tasks_by_status_paged(token, status))

    out = _rows_to_tasks_df(all_rows, workers_df, preferred_lang, token, progress_cb)
    if out.empty:
        return out
    if "archived" in out.columns:
        out = out[~out["archived"].astype(bool)].reset_index(drop=True)
    return out


def fetch_archived_tasks_since(
    since_date,
    workers_df: pd.DataFrame | None = None,
    preferred_lang: str = "es",
    progress_cb=None,
) -> pd.DataFrame:
    """Trae tareas finalizadas/archivadas con datetime o deadline >= since_date.

    Se descargan por status ("finalizada") y también los archivados que
    aparecen dentro de statuses no finales. Se filtran por fecha ANTES de
    enriquecer con detalles para minimizar llamadas al endpoint /tasks/{id}.
    """
    since_ts = pd.Timestamp(since_date)
    token = get_token()

    all_rows: list[dict] = []
    statuses = tuple(STATUSES_FINAL) + tuple(STATUSES_NO_FINAL)
    for i, status in enumerate(statuses):
        if progress_cb:
            progress_cb(f"Descargando archivadas status={status}", i, len(statuses))
        all_rows.extend(get_tasks_by_status_paged(token, status))
    if not all_rows:
        return pd.DataFrame()

    def _row_matches(r: dict) -> bool:
        is_final = str(r.get("status") or "").strip().lower() == "finalizada"
        is_arch = _coerce_archived(r.get("archived"))
        if not (is_final or is_arch):
            return False
        dt_val = pd.to_datetime(r.get("datetime"), errors="coerce", utc=True)
        dl_val = pd.to_datetime(r.get("deadline"), errors="coerce", utc=True)
        cutoff = since_ts.tz_localize("UTC") if since_ts.tzinfo is None else since_ts
        cand = [x for x in (dt_val, dl_val) if pd.notna(x)]
        if not cand:
            return False
        return max(cand) >= cutoff

    filtered_rows = [r for r in all_rows if _row_matches(r)]
    if not filtered_rows:
        return pd.DataFrame()

    return _rows_to_tasks_df(filtered_rows, workers_df, preferred_lang, token, progress_cb)


def patch_task_dates(token: str, task_id: str, datetime_utc: str, deadline_utc: str) -> dict:
    body = {"datetime": datetime_utc, "deadline": deadline_utc}
    try:
        r = requests.patch(
            f"{API_BASE}/tasks/{task_id}",
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        return {
            "id": str(task_id),
            "status": r.status_code,
            "ok": r.status_code < 400,
            "body": r.text[:500],
        }
    except requests.RequestException as e:
        return {"id": str(task_id), "status": None, "ok": False, "body": str(e)}


def push_tasks_to_cor(payload_df: pd.DataFrame, progress_cb=None) -> dict:
    if payload_df is None or len(payload_df) == 0:
        return {"ok": 0, "fail": 0, "results": []}
    token = get_token()
    results: list[dict] = []
    ok_count = 0
    fail_count = 0
    total = len(payload_df)
    for i, row in enumerate(payload_df.itertuples(index=False), start=1):
        res = patch_task_dates(token, row.id, row.datetime, row.deadline)
        results.append(res)
        if res.get("ok"):
            ok_count += 1
        else:
            fail_count += 1
        if progress_cb:
            progress_cb(i, total, res)
    return {"ok": ok_count, "fail": fail_count, "results": results}
