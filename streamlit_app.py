from __future__ import annotations

import datetime as dt
import io
import traceback
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from domain.comments import (
    COMMENT_COLS,
    empty_comments_df,
    load_comments_file,
    resolve_comments_dir,
    save_comments_file,
)
from domain.config import (
    ALL_SENTINEL,
    BRAND_PALETTE,
    DEFAULT_STATUS_COLOR,
    STATUS_COLORS,
    STATUS_DISPLAY,
    TASK_FALLBACK_HOURS,
    TZ_LOCAL,
    WORK_HOURS_RATIO,
)
from domain.country import COUNTRY_CATALOG, build_country_choices, extract_country_series
from domain.data_loader import load_sla, load_workers_allowed, load_workers_pods
from domain.filters import apply_gantt_filters
from domain.pack import pack_all_resources_no_gaps
from domain.pods import attach_pod_to_planned, build_email_to_pod
from domain.payload import (
    build_planned_from_a_plan,
    build_push_payload_editable,
    build_push_payload_original,
    detect_business_unit,
)
from components.frappe_gantt import frappe_gantt

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
ASSETS_DIR = APP_DIR / "assets"

st.set_page_config(
    page_title="Planificador de diseño",
    page_icon=":material/schedule:",
    layout="wide",
)


def _read_svg(name: str) -> str:
    p = ASSETS_DIR / "icons" / name
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _svg_recolor(svg: str, fill: str) -> str:
    import re as _re
    if not svg:
        return svg
    svg = _re.sub(r"<style[\s\S]*?</style>", "", svg)
    svg = _re.sub(r'\sclass="[^"]*"', "", svg)
    svg = _re.sub(r'\sfill="[^"]*"', "", svg)
    svg = svg.replace("<svg ", f'<svg fill="{fill}" ', 1)
    return svg


LOGO_TIGO_WHITE = _svg_recolor(_read_svg("logo-tigo.svg"), "#FFFFFF")
GO_WHITE = _svg_recolor(_read_svg("go.svg"), "#FFFFFF")


def _password_gate() -> None:
    expected = ""
    try:
        expected = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        expected = ""
    if not expected:
        return
    if st.session_state.get("_auth_ok"):
        return

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800;900&display=swap');

        .stApp {
          background:
            radial-gradient(1200px 600px at 15% -10%, rgba(68,200,245,.28), transparent 60%),
            radial-gradient(900px 500px at 110% 110%, rgba(0,38,229,.35), transparent 60%),
            linear-gradient(135deg, #00005A 0%, #001EB4 60%, #0026E5 100%) !important;
          min-height: 100vh;
        }
        header[data-testid="stHeader"] { display:none !important; }
        section[data-testid="stSidebar"] { display:none !important; }
        div[data-testid="stToolbar"] { display:none !important; }

        div[data-testid="stMainBlockContainer"] {
          max-width: 440px !important;
          padding: 12vh 0 4vh 0 !important;
        }
        div[data-testid="stMainBlockContainer"] > div > div {
          background: #FFFFFF;
          border-radius: 28px;
          padding: 44px 40px 32px 40px;
          box-shadow:
            0 40px 100px rgba(0,0,20,.45),
            0 12px 32px rgba(0,0,20,.25);
          font-family: 'DM Sans','Segoe UI',sans-serif;
        }

        .gate-brand {
          display:flex; align-items:center; justify-content:center;
          gap:10px; margin-bottom: 22px;
        }
        .gate-brand__box {
          display:inline-flex; align-items:center; justify-content:center;
          padding: 12px 16px;
          background: linear-gradient(135deg, #001EB4 0%, #00005A 100%);
          border-radius: 14px;
          box-shadow: 0 6px 18px rgba(0,30,180,.35);
        }
        .gate-brand__box svg { height: 22px; width:auto; display:block; }
        .gate-ey {
          text-align:center;
          font-size: 10px; font-weight: 800; letter-spacing: 2.5px;
          text-transform: uppercase; color: #0026E5;
        }
        .gate-title {
          text-align:center;
          font-family:'DM Sans',sans-serif;
          font-weight: 900; font-size: 24px; line-height: 1.15;
          color: #00005A; letter-spacing:-.3px;
          margin: 6px 0 10px 0;
        }
        .gate-hint {
          text-align:center;
          color: #64748B; font-size: 13.5px; line-height: 1.5;
          margin: 0 0 22px 0;
        }

        div[data-testid="stForm"] {
          border: none !important;
          padding: 0 !important;
          background: transparent !important;
        }
        div[data-testid="stForm"] div[data-baseweb="input"] > div {
          border-radius: 14px !important;
          border: 2px solid #E2E8F0 !important;
          background: #F8FAFC !important;
          min-height: 52px !important;
          transition: border-color .15s, background .15s, box-shadow .15s;
        }
        div[data-testid="stForm"] div[data-baseweb="input"] > div:hover {
          border-color: #CBD5E1 !important;
          background: #FFFFFF !important;
        }
        div[data-testid="stForm"] div[data-baseweb="input"] > div:focus-within {
          border-color: #001EB4 !important;
          background: #FFFFFF !important;
          box-shadow: 0 0 0 4px rgba(0,30,180,.14) !important;
        }
        div[data-testid="stForm"] div[data-baseweb="input"] input {
          font-family: 'DM Sans','Segoe UI',sans-serif !important;
          font-size: 15px !important;
          font-weight: 500 !important;
          color: #0B1220 !important;
          padding: 0 16px !important;
        }
        div[data-testid="stForm"] div[data-baseweb="input"] input::placeholder {
          color: #94A3B8 !important;
          font-weight: 400 !important;
        }

        div[data-testid="stForm"] .stFormSubmitButton { margin-top: 6px; }
        div[data-testid="stForm"] .stFormSubmitButton > button {
          width: 100% !important;
          min-height: 48px !important;
          border-radius: 9999px !important;
          background: linear-gradient(135deg, #001EB4 0%, #00005A 100%) !important;
          color: #FFFFFF !important;
          border: none !important;
          font-family: 'DM Sans','Segoe UI',sans-serif !important;
          font-weight: 700 !important;
          font-size: 11px !important;
          letter-spacing: .8px;
          text-transform: uppercase;
          white-space: nowrap;
          box-shadow: 0 8px 20px rgba(0,30,180,.35);
          transition: transform .08s, box-shadow .15s, filter .15s;
        }
        div[data-testid="stForm"] .stFormSubmitButton > button > div,
        div[data-testid="stForm"] .stFormSubmitButton > button p {
          font-size: 11px !important;
          font-weight: 700 !important;
          letter-spacing: .8px !important;
          white-space: nowrap !important;
        }
        div[data-testid="stForm"] .stFormSubmitButton > button:hover {
          transform: translateY(-1px);
          box-shadow: 0 12px 26px rgba(0,30,180,.45);
          filter: brightness(1.05);
        }
        div[data-testid="stForm"] .stFormSubmitButton > button:active {
          transform: translateY(0);
          box-shadow: 0 6px 14px rgba(0,30,180,.35);
        }

        div[data-testid="stAlert"] {
          border-radius: 14px !important;
          border: none !important;
          margin-top: 12px !important;
        }

        .gate-foot {
          text-align:center;
          color: #94A3B8; font-size: 10px;
          margin-top: 22px;
          letter-spacing: 2px; text-transform: uppercase; font-weight: 700;
        }
        .gate-foot__dot {
          display:inline-block; width:4px; height:4px; border-radius:50%;
          background:#CBD5E1; margin: 0 8px; vertical-align: middle;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="gate-brand">
          <div class="gate-brand__box">{LOGO_TIGO_WHITE}</div>
        </div>
        <div class="gate-ey">Design · Regional</div>
        <div class="gate-title">Planificador de diseño</div>
        <div class="gate-hint">Ingresa la contraseña del equipo para continuar.</div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("_pwd_form", clear_on_submit=False):
        pwd = st.text_input(
            "Contraseña",
            type="password",
            key="_pwd_input",
            label_visibility="collapsed",
            placeholder="Contraseña",
        )
        submitted = st.form_submit_button("Entrar")

    if submitted:
        if pwd == expected:
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")

    st.markdown(
        '<div class="gate-foot">Internal<span class="gate-foot__dot"></span>Tigo</div>',
        unsafe_allow_html=True,
    )
    st.stop()


_password_gate()

# ---------- Session state ----------

def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("planned_tasks", pd.DataFrame())
    ss.setdefault("planned_resources", pd.DataFrame(columns=["resource_id", "resource_name"]))
    ss.setdefault("a_plan", pd.DataFrame())
    ss.setdefault("a_plan_baseline", pd.DataFrame(columns=["id", "datetime", "deadline"]))
    ss.setdefault("archived_tasks", pd.DataFrame())
    ss.setdefault("log", "Listo. Primero sincroniza con ProjectCor.")
    ss.setdefault("selected_id", None)
    ss.setdefault("comments_df", empty_comments_df())
    ss.setdefault("comments_path", None)
    ss.setdefault("comments_dir_used", None)
    ss.setdefault("publish_confirm_open", False)
    ss.setdefault("publish_view", None)
    ss.setdefault("publish_payload", None)
    ss.setdefault("filter_resource", ALL_SENTINEL)
    ss.setdefault("filter_country", [])
    ss.setdefault("filter_objective", ALL_SENTINEL)
    ss.setdefault("filter_task_type", "Todas")
    ss.setdefault("filter_business_unit", ALL_SENTINEL)
    ss.setdefault("filter_date_from", None)
    ss.setdefault("filter_date_to", None)
    ss.setdefault("sync_anchor_date", dt.date.today())


_init_state()


@st.cache_resource(show_spinner=False)
def _resolve_comments():
    comments_dir, _ = resolve_comments_dir(APP_DIR)
    return comments_dir


if st.session_state["comments_path"] is None:
    try:
        cdir = _resolve_comments()
        st.session_state["comments_path"] = cdir / "comments_log.csv"
        st.session_state["comments_dir_used"] = str(cdir)
        st.session_state["comments_df"] = load_comments_file(st.session_state["comments_path"])
    except Exception as e:
        st.session_state["log"] = f"[WARN] No se pudo inicializar comentarios: {e}"


@st.cache_data(show_spinner=False)
def _load_reference_data():
    return {
        "sla": load_sla(DATA_DIR / "sla.csv"),
        "workers_allowed": load_workers_allowed(DATA_DIR / "workers_allowed.csv"),
        "workers_pods": load_workers_pods(DATA_DIR / "workers_pods.csv"),
    }


# ---------- Helpers ----------

def _log(msg: str) -> None:
    st.session_state["log"] = msg


def _distinct_choices(df: pd.DataFrame, col: str) -> list[str]:
    if df is None or df.empty or col not in df.columns:
        return []
    s = df[col].fillna("").astype(str).str.strip()
    return sorted({x for x in s if x})


def _filtered_planned_tasks() -> pd.DataFrame:
    try:
        return apply_gantt_filters(
            st.session_state.get("planned_tasks", pd.DataFrame()),
            resource=st.session_state.get("filter_resource"),
            countries=st.session_state.get("filter_country") or [],
            objective=st.session_state.get("filter_objective"),
            business_unit=st.session_state.get("filter_business_unit"),
            date_from=st.session_state.get("filter_date_from"),
            date_to=st.session_state.get("filter_date_to"),
        )
    except Exception as _e:
        st.session_state["log"] = f"[filtro] {type(_e).__name__}: {_e}"
        return st.session_state.get("planned_tasks", pd.DataFrame())


def _prepare_aplan_gantt_view(ap: pd.DataFrame) -> pd.DataFrame:
    if ap is None or ap.empty:
        return pd.DataFrame()
    df = ap.copy()
    for col in ("tag", "status", "project_name", "skill_names"):
        if col not in df.columns:
            df[col] = ""
    for col in ("collab_email_plan", "collab_email"):
        if col not in df.columns:
            df[col] = ""
    df["id"] = df["id"].astype(str)
    df["text"] = df["title"].fillna("").astype(str) if "title" in df.columns else ""
    ep = df["collab_email_plan"].fillna("").astype(str).str.strip()
    ec = df["collab_email"].fillna("").astype(str).str.strip()
    df["resource_id"] = ep.where(ep != "", ec).where(ep.ne("") | ec.ne(""), "SIN_ASIGNAR")
    df["resource_id"] = df["resource_id"].replace("", "SIN_ASIGNAR")
    start = pd.to_datetime(df["datetime"], errors="coerce")
    end = pd.to_datetime(df["deadline"], errors="coerce")
    dur = (end - start).dt.total_seconds() / 3600.0
    dur = dur.where(dur.notna() & (dur > 0), TASK_FALLBACK_HOURS)
    df["start_date"] = start.dt.strftime("%Y-%m-%d %H:%M")
    df["duration"] = dur.astype(float)
    df["status"] = df["status"].fillna("").astype(str)
    df["objetivo"] = detect_business_unit(df["skill_names"])
    df["business_unit"] = df["project_name"].fillna("").astype(str)
    df["pais"] = extract_country_series(df["tag"])
    df["resource_name"] = df["resource_id"]
    df = df[df["start_date"].notna() & (df["start_date"] != "")]
    filtered = apply_gantt_filters(
        df,
        resource=st.session_state["filter_resource"],
        countries=st.session_state["filter_country"],
        objective=st.session_state["filter_objective"],
        business_unit=st.session_state["filter_business_unit"],
        date_from=st.session_state["filter_date_from"],
        date_to=st.session_state["filter_date_to"],
    )
    type_choice = st.session_state.get("filter_task_type", "Todas")
    if type_choice != "Todas" and "typeTask_name" in filtered.columns:
        is_creativ = filtered["typeTask_name"].fillna("").astype(str).str.contains("creativ", case=False, na=False)
        filtered = filtered[is_creativ] if type_choice == "Creatividad" else filtered[~is_creativ]
    return filtered.sort_values(["resource_id", "start_date", "id"]).reset_index(drop=True)


def _clean_collab_series(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.replace(r"@millicom\.com", "", regex=True, case=False)
        .str.replace(".", " ", regex=False)
        .str.title()
    )


def _to_timeline_df(tasks: pd.DataFrame) -> pd.DataFrame:
    if tasks is None or tasks.empty:
        return pd.DataFrame()
    df = tasks.copy()
    df["_start"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["_dur"] = pd.to_numeric(df["duration"], errors="coerce").fillna(TASK_FALLBACK_HOURS)
    df["_end"] = df["_start"] + pd.to_timedelta(df["_dur"], unit="h")
    df = df[df["_start"].notna()]
    df["Estado"] = df.get("status", "").fillna("").astype(str).map(lambda s: STATUS_DISPLAY.get(s, s))
    df["Tarea"] = df.get("text", "").fillna("").astype(str)
    df["Colaborador"] = _clean_collab_series(df["resource_name"].fillna(df["resource_id"]))
    return df


def _plot_gantt(tasks: pd.DataFrame, key: str) -> None:
    df = _to_timeline_df(tasks)
    if df.empty:
        st.info("No hay tareas para mostrar con los filtros actuales.")
        return
    color_map = {STATUS_DISPLAY.get(k, k): v for k, v in STATUS_COLORS.items()}
    color_map[""] = DEFAULT_STATUS_COLOR
    fig = px.timeline(
        df,
        x_start="_start",
        x_end="_end",
        y="Colaborador",
        color="Estado",
        color_discrete_map=color_map,
        hover_data={
            "Tarea": True,
            "id": True,
            "_start": "|%Y-%m-%d %H:%M",
            "_end": "|%Y-%m-%d %H:%M",
            "_dur": ":.1f",
            "Colaborador": False,
            "Estado": True,
        },
        custom_data=["id"],
    )
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title=None)
    fig.update_traces(marker_line_color="white", marker_line_width=1.2)
    fig.update_layout(
        height=max(360, 28 * df["Colaborador"].nunique() + 120),
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, key=key)


def _to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="tasks")
    return buf.getvalue()


def _tasks_for_frappe_gantt(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    d = df.copy().reset_index(drop=True)
    starts = pd.to_datetime(d["start_date"], errors="coerce")
    durs = pd.to_numeric(d["duration"], errors="coerce").fillna(TASK_FALLBACK_HOURS)
    ends = starts + pd.to_timedelta(durs, unit="h")
    out = []
    for i in range(len(d)):
        s = starts.iloc[i]; e = ends.iloc[i]
        if pd.isna(s) or pd.isna(e):
            continue
        if (e - s).total_seconds() < 3600:
            e = s + pd.Timedelta(hours=1)
        row = d.iloc[i]
        name = str(row.get("text") or row.get("title") or "")
        rid = str(row.get("resource_name") or row.get("resource_id") or "")
        display = f"{name[:60]} · {rid}" if rid else name[:60]
        out.append({
            "id": str(row.get("id", "")),
            "name": display,
            "start": s.strftime("%Y-%m-%d"),
            "end": e.strftime("%Y-%m-%d"),
            "progress": 0,
        })
    return out


def _bh_next_moment(dt: pd.Timestamp) -> pd.Timestamp:
    if pd.isna(dt):
        return dt
    while dt.weekday() >= 5:
        dt = (dt + pd.Timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    if dt.hour < 9:
        dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if dt.hour >= 17 or (dt.hour == 17 and dt.minute > 0):
        dt = (dt + pd.Timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        while dt.weekday() >= 5:
            dt = (dt + pd.Timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return dt


def _bh_end(start_dt: pd.Timestamp, work_hours: float) -> pd.Timestamp:
    if pd.isna(start_dt) or work_hours is None or work_hours <= 0:
        return start_dt
    remaining = float(work_hours)
    cur = _bh_next_moment(pd.Timestamp(start_dt))
    while remaining > 0:
        day_end = cur.replace(hour=17, minute=0, second=0, microsecond=0)
        avail = (day_end - cur).total_seconds() / 3600.0
        if avail <= 0:
            cur = _bh_next_moment(cur + pd.Timedelta(minutes=1))
            continue
        if remaining <= avail:
            return cur + pd.Timedelta(hours=remaining)
        remaining -= avail
        cur = _bh_next_moment((cur + pd.Timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0))
    return cur


def _apply_date_change(kind: str, tid: str, iso_start: str, iso_end: str) -> None:
    ss_key = "planned_tasks" if kind == "final" else "a_plan"
    df = st.session_state.get(ss_key)
    if df is None or df.empty:
        return
    mask = df["id"].astype(str) == str(tid)
    if not mask.any():
        return
    new_start = pd.to_datetime(iso_start, errors="coerce")
    new_end = pd.to_datetime(iso_end, errors="coerce")
    if pd.isna(new_start) or pd.isna(new_end):
        return
    df = df.copy()
    if kind == "final":
        df.loc[mask, "start_date"] = new_start.strftime("%Y-%m-%d %H:%M")
        dur_h = max(1.0, (new_end - new_start).total_seconds() / 3600.0)
        df.loc[mask, "duration"] = dur_h
    else:
        df.loc[mask, "datetime"] = new_start.strftime("%Y-%m-%d %H:%M:%S")
        df.loc[mask, "deadline"] = new_end.strftime("%Y-%m-%d %H:%M:%S")
    st.session_state[ss_key] = df


# ---------- Tabla estandar (8 columnas) ----------

DISPLAY_COLS = [
    "Cliente", "Proyecto", "Tarea", "ID de tarea",
    "Estado", "Inicio", "Final", "Categoría",
]


def _fmt_short_date(s):
    return pd.to_datetime(s, errors="coerce").dt.strftime("%y-%m-%d").fillna("")


def _to_client(cc) -> str:
    s = str(cc or "").strip().upper()
    if not s:
        return ""
    return f"tigo {COUNTRY_CATALOG.get(s, s)}"


def _build_display_from_planned(pt: pd.DataFrame) -> pd.DataFrame:
    if pt is None or pt.empty:
        return pd.DataFrame(columns=DISPLAY_COLS)
    d = pt.copy()
    starts = pd.to_datetime(d.get("start_date", ""), errors="coerce")
    durs = pd.to_numeric(d.get("duration", TASK_FALLBACK_HOURS), errors="coerce").fillna(TASK_FALLBACK_HOURS)
    ends = starts + pd.to_timedelta(durs, unit="h")
    return pd.DataFrame({
        "Cliente": d.get("pais", pd.Series([""] * len(d))).fillna("").map(_to_client),
        "Proyecto": d.get("business_unit", pd.Series([""] * len(d))).fillna("").astype(str),
        "Tarea": d.get("text", pd.Series([""] * len(d))).fillna("").astype(str),
        "ID de tarea": d.get("id", pd.Series([""] * len(d))).astype(str),
        "Estado": d.get("status", pd.Series([""] * len(d))).fillna("").astype(str).map(lambda s: STATUS_DISPLAY.get(s, s)),
        "Inicio": _fmt_short_date(d.get("start_date", pd.Series([""] * len(d)))),
        "Final": ends.dt.strftime("%y-%m-%d").fillna(""),
        "Categoría": d.get("typeTask_name", pd.Series([""] * len(d))).fillna("").astype(str),
    })


def _build_display_from_aplan(ap: pd.DataFrame) -> pd.DataFrame:
    if ap is None or ap.empty:
        return pd.DataFrame(columns=DISPLAY_COLS)
    d = ap.copy()
    tags = d.get("tag", pd.Series([""] * len(d))).fillna("").astype(str)
    countries = extract_country_series(tags)
    return pd.DataFrame({
        "Cliente": countries.map(_to_client),
        "Proyecto": d.get("project_name", pd.Series([""] * len(d))).fillna("").astype(str),
        "Tarea": d.get("title", pd.Series([""] * len(d))).fillna("").astype(str),
        "ID de tarea": d.get("id", pd.Series([""] * len(d))).astype(str),
        "Estado": d.get("status", pd.Series([""] * len(d))).fillna("").astype(str).map(lambda s: STATUS_DISPLAY.get(s, s)),
        "Inicio": _fmt_short_date(d.get("datetime", pd.Series([""] * len(d)))),
        "Final": _fmt_short_date(d.get("deadline", pd.Series([""] * len(d)))),
        "Categoría": d.get("typeTask_name", pd.Series([""] * len(d))).fillna("").astype(str),
    })


_MULTISELECT_COLS = ("Cliente", "Proyecto", "Estado", "Categoría")
_NO_FILTER_COLS = ("Inicio", "Final")


def _column_filters(df: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    st.markdown(
        "<div class='col-filter-bar-label'>Filtrar por columna</div>",
        unsafe_allow_html=True,
    )
    st.markdown(f"<div class='col-filter-bar' data-key='{key_prefix}'></div>", unsafe_allow_html=True)
    header_cols = st.columns(len(DISPLAY_COLS), gap="small")
    active: dict[str, tuple[str, object]] = {}
    for i, col in enumerate(DISPLAY_COLS):
        state_key = f"{key_prefix}_flt_{col}"
        with header_cols[i]:
            if col in _NO_FILTER_COLS:
                continue
            if col in _MULTISELECT_COLS:
                opts = sorted({x for x in df[col].dropna().astype(str).tolist() if x.strip()})
                sel = st.multiselect(
                    col, opts, key=state_key,
                    placeholder=col, label_visibility="collapsed",
                )
                if sel:
                    active[col] = ("in", sel)
            else:
                val = st.text_input(
                    col, key=state_key,
                    placeholder=col, label_visibility="collapsed",
                )
                if val:
                    active[col] = ("contains", val)
    out = df
    for col, (kind, val) in active.items():
        if kind == "in":
            out = out[out[col].astype(str).isin(val)]
        else:
            out = out[out[col].astype(str).str.contains(str(val), case=False, na=False, regex=False)]
    return out


_LABEL_TO_COLOR = {STATUS_DISPLAY.get(code, code): color for code, color in STATUS_COLORS.items()}


def _style_by_status(df: pd.DataFrame):
    def _row_style(row):
        label = str(row.get("Estado", "")).strip()
        color = _LABEL_TO_COLOR.get(label) or STATUS_COLORS.get(label.lower().replace(" ", "_"), "")
        if not color or not color.startswith("#") or len(color) < 7:
            return [""] * len(row)
        try:
            r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        except ValueError:
            return [""] * len(row)
        return [f"background-color: rgba({r},{g},{b},0.18); color:#111;"] * len(row)
    return df.style.apply(_row_style, axis=1)


def _render_task_table(df_display: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    filtered = _column_filters(df_display, key_prefix)
    if filtered is None or filtered.empty:
        st.info("No hay tareas que coincidan con los filtros.")
        return filtered if filtered is not None else pd.DataFrame(columns=DISPLAY_COLS)
    st.dataframe(
        _style_by_status(filtered),
        hide_index=True,
        width="stretch",
        height=520,
        column_config={
            "Cliente": st.column_config.TextColumn(width="small"),
            "Proyecto": st.column_config.TextColumn(width="small"),
            "Tarea": st.column_config.TextColumn(width="medium"),
            "ID de tarea": st.column_config.TextColumn(width="small"),
            "Estado": st.column_config.TextColumn(width="small"),
            "Inicio": st.column_config.TextColumn(width="small"),
            "Final": st.column_config.TextColumn(width="small"),
            "Categoría": st.column_config.TextColumn(width="small"),
        },
    )
    return filtered


# ---------- Global styles (Tigo Design System v2) ----------


def _inject_css(path: Path) -> None:
    try:
        css = path.read_text(encoding="utf-8")
    except Exception:
        return
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


_inject_css(ASSETS_DIR / "tigo.css")

# ---------- Header (topbar con LOGO TIGO + eyebrow + badge GO) ----------

_header_html = f"""
<div class="tigo-topbar">
  <div class="tigo-topbar__inner">
    <div class="tigo-brand">
      <div class="tigo-brand__logo">{LOGO_TIGO_WHITE}</div>
      <div class="tigo-brand__sep"></div>
      <div class="tigo-brand__text">
        <div class="tigo-brand__eyebrow">Design · Regional</div>
        <div class="tigo-brand__title">Planificador de diseño</div>
      </div>
    </div>
    <div class="tigo-badge"><span class="tigo-badge__go">{GO_WHITE}</span>Internal</div>
  </div>
</div>
"""
st.markdown(_header_html, unsafe_allow_html=True)


# ---------- Sidebar (Panel de trabajo) ----------

with st.sidebar:
    st.markdown("### Panel de trabajo")
    st.caption("Sincroniza, filtra y ajusta el cronograma")

    planned_tasks = st.session_state["planned_tasks"]
    ap = st.session_state["a_plan"]
    resource_options = [(ALL_SENTINEL, "Todos")]
    if not planned_tasks.empty and "resource_id" in planned_tasks.columns:
        pairs = planned_tasks[["resource_id", "resource_name"]].drop_duplicates().sort_values("resource_name")
        resource_options.extend((r, n or r) for r, n in zip(pairs["resource_id"], pairs["resource_name"]))
    st.selectbox(
        "Colaborador",
        options=[o[0] for o in resource_options],
        format_func=lambda v: dict(resource_options).get(v, v),
        key="filter_resource",
    )

    country_choices = build_country_choices(planned_tasks, st.session_state["comments_df"])
    country_options = list(country_choices.values())
    st.multiselect(
        "País (vacío = todos)",
        options=country_options,
        format_func=lambda code: next((lbl for lbl, c in country_choices.items() if c == code), code),
        key="filter_country",
        placeholder="Selecciona uno o varios países…",
    )

    obj_choices = _distinct_choices(planned_tasks, "objetivo")
    st.selectbox(
        "Objetivo",
        options=[ALL_SENTINEL, *obj_choices],
        format_func=lambda v: "Todas" if v == ALL_SENTINEL else v,
        key="filter_objective",
    )

    bu_choices = _distinct_choices(planned_tasks, "business_unit")
    st.selectbox(
        "Unidad de negocio",
        options=[ALL_SENTINEL, *bu_choices],
        format_func=lambda v: "Todas" if v == ALL_SENTINEL else v,
        key="filter_business_unit",
    )

    st.selectbox(
        "Tipo de tarea",
        options=["Todas", "Creatividad", "No creatividad"],
        key="filter_task_type",
    )

    col_df, col_dt = st.columns(2)
    with col_df:
        st.date_input("Desde", key="filter_date_from", format="YYYY-MM-DD")
    with col_dt:
        st.date_input("Hasta", key="filter_date_to", format="YYYY-MM-DD")

    st.markdown("")
    st.date_input(
        "Fecha ancla del cronograma",
        key="sync_anchor_date",
        format="YYYY-MM-DD",
        help="Desde qué fecha se planifica al sincronizar con ProjectCor.",
    )

    def _do_sync():
        try:
            from domain.cor_client import fetch_a_plan_and_archived
            from domain.scheduler import add_sla_to_a, schedule_tasks_from_today
            refs = _load_reference_data()
            status_box = st.status("Sincronizando con ProjectCor…", expanded=True)
            with status_box:
                progress_msg = st.empty()
                progress_bar = st.progress(0)

                def _on_progress(msg, done, total):
                    progress_msg.write(msg)
                    if total and total > 0:
                        pct = max(0, min(100, int(done * 100 / total)))
                        progress_bar.progress(pct)

                a, archived_df = fetch_a_plan_and_archived(
                    dt.date(2026, 5, 1),
                    workers_df=refs["workers_pods"],
                    progress_cb=_on_progress,
                )
                progress_msg.write("Calculando SLA y cronograma…")
                progress_bar.progress(100)
                a = add_sla_to_a(a, refs["sla"])
                res = schedule_tasks_from_today(
                    a, refs["workers_allowed"],
                    anchor_date=st.session_state.get("sync_anchor_date") or dt.date.today(),
                )
                status_box.update(
                    label=f"Sincronización lista · {len(res['a_plan'])} tareas · {len(archived_df)} archivadas",
                    state="complete",
                )
            ap_new = res["a_plan"]
            st.session_state["a_plan"] = ap_new
            st.session_state["archived_tasks"] = archived_df
            if not ap_new.empty:
                st.session_state["a_plan_baseline"] = ap_new[["id", "datetime", "deadline"]].assign(id=lambda d: d["id"].astype(str))
            else:
                st.session_state["a_plan_baseline"] = pd.DataFrame(columns=["id", "datetime", "deadline"])
            _log(
                f"Sincronizado con ProjectCor. Tareas: {len(ap_new)} · archivadas desde 2026-05-01: {len(archived_df)}\n"
                "Siguiente paso: dibujar el cronograma."
            )
        except Exception as e:
            tb = traceback.format_exc(limit=6)
            _log(f"ERROR sincronización: {e}\n{tb}")
            st.error(f"Error al sincronizar: {e}")
            with st.expander("Traceback"):
                st.code(tb, language="text")

    def _do_draw():
        ap_now = st.session_state["a_plan"]
        if ap_now is None or ap_now.empty:
            st.warning("Primero usa 'Sincronizar con ProjectCor'.")
            return
        planned = build_planned_from_a_plan(ap_now)
        if planned["tasks"].empty:
            _log("No hay tareas con fechas planeadas para dibujar.")
            st.warning("No hay tareas planeadas para dibujar.")
            return
        st.session_state["planned_tasks"] = planned["tasks"]
        st.session_state["planned_resources"] = planned["resources"]
        _log(
            f"Cronograma dibujado. Tareas: {len(planned['tasks'])} · "
            f"Colaboradores: {len(planned['resources'])}"
        )

    def _do_pack():
        pt = st.session_state["planned_tasks"]
        if pt is None or pt.empty:
            st.warning("Primero dibuja el cronograma antes de compactar.")
            return
        st.session_state["planned_tasks"] = pack_all_resources_no_gaps(pt)
        _log("Cronograma compactado sin huecos. COMODÍN colocado primero por colaborador.")

    def _do_publish_open():
        tabs_active = st.session_state.get("_active_publish_tab", "Cronograma editable")
        if tabs_active == "Cronograma editable":
            pt = _filtered_planned_tasks()
            if pt.empty:
                st.warning("No hay tareas en la vista filtrada actual.")
                return
            payload = build_push_payload_editable(pt)
            if payload.empty:
                st.warning("No hay fechas válidas para publicar.")
                return
            st.session_state["publish_view"] = "Cronograma editable"
            st.session_state["publish_payload"] = payload
            st.session_state["publish_confirm_open"] = True
        elif tabs_active == "Plan original":
            ap_now = st.session_state["a_plan"]
            if ap_now is None or ap_now.empty:
                st.warning("No hay plan original. Sincroniza primero.")
                return
            ap_view = _prepare_aplan_gantt_view(ap_now)
            if ap_view.empty:
                st.warning("No hay tareas en la vista filtrada actual.")
                return
            filtered_ap = ap_now[ap_now["id"].astype(str).isin(ap_view["id"].astype(str))]
            payload = build_push_payload_original(filtered_ap, st.session_state["a_plan_baseline"])
            if payload.empty:
                st.info("No hay cambios en la vista filtrada para publicar.")
                return
            st.session_state["publish_view"] = "Plan original"
            st.session_state["publish_payload"] = payload
            st.session_state["publish_confirm_open"] = True
        else:
            st.warning("Abre 'Cronograma editable' o 'Plan original' antes de publicar.")

    st.button(
        "Sincronizar con ProjectCor",
        type="primary",
        icon=":material/sync:",
        on_click=_do_sync,
    )
    st.button(
        "Dibujar cronograma",
        icon=":material/draw:",
        on_click=_do_draw,
    )
    st.button(
        "Compactar sin huecos",
        icon=":material/compress:",
        on_click=_do_pack,
    )
    st.button(
        "Publicar fechas en ProjectCor",
        icon=":material/cloud_upload:",
        on_click=_do_publish_open,
    )

    export_df = None
    pt_now = _filtered_planned_tasks()
    if not pt_now.empty:
        export_df = pd.DataFrame({
            "País": pt_now.get("pais", ""),
            "BU": pt_now.get("business_unit", ""),
            "Text (nombre de la tarea)": pt_now.get("text", ""),
            "Type Task name": pt_now.get("typeTask_name", ""),
            "Skill Main": pt_now.get("skill_main", ""),
            "Objetivo": pt_now.get("objetivo", ""),
            "Fecha de inicio de la tarea": pt_now.get("start_date", ""),
            "Duración (en días)": (pd.to_numeric(pt_now.get("duration", 0), errors="coerce") / 24).round(2),
            "Id (número de tarea)": pt_now.get("id", ""),
        })
    st.download_button(
        "Exportar a Excel",
        data=_to_excel_bytes(export_df if export_df is not None else pd.DataFrame({"Mensaje": ["No hay tareas para exportar."]})),
        file_name=f"gantt_tasks_{dt.date.today():%Y%m%d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        icon=":material/download:",
    )

    st.divider()

    with st.container(border=True):
        st.markdown("**Detalle de tarea**")
        sel_id = st.session_state["selected_id"]
        pt = st.session_state["planned_tasks"]
        if sel_id is None or pt is None or pt.empty:
            st.caption("Selecciona una tarea en la pestaña 'Cronograma editable' para ver su detalle.")
        else:
            row = pt[pt["id"].astype(str) == str(sel_id)]
            if row.empty:
                st.caption(f"No se encontró el id: {sel_id}")
            else:
                r = row.iloc[0]
                for k, v in [
                    ("Título", r.get("text", "")),
                    ("Colaborador", r.get("resource_name", "")),
                    ("Inicio", r.get("start_date", "")),
                    ("Duración (h)", r.get("duration", "")),
                    ("Skill", r.get("skill_main", "")),
                    ("Objetivo", r.get("objetivo", "")),
                    ("Unidad de negocio", r.get("business_unit", "")),
                    ("Tipo de tarea", r.get("typeTask_name", "")),
                    ("Etiqueta", r.get("tag", "")),
                    ("País", r.get("pais", "")),
                    ("Estado", STATUS_DISPLAY.get(str(r.get("status", "")), r.get("status", ""))),
                    ("Prioridad", r.get("priority", "")),
                ]:
                    st.markdown(f"**{k}:** {v}")
                desc = r.get("description", "")
                if desc:
                    st.markdown("**Descripción:**")
                    st.markdown(f"<div style='font-size:12px;opacity:.85;'>{desc}</div>", unsafe_allow_html=True)


# ---------- Publish confirmation modal ----------

if st.session_state["publish_confirm_open"]:
    payload = st.session_state["publish_payload"]
    view = st.session_state["publish_view"]

    @st.dialog(f"Publicar {len(payload)} tarea(s) en ProjectCor")
    def _publish_dialog():
        st.markdown(
            f"Vas a **actualizar fechas** (`datetime` y `deadline`) en ProjectCor de la vista **{view}**."
        )
        st.caption(f"Total: {len(payload)} tareas. Esta acción no es reversible automáticamente.")
        st.dataframe(payload, hide_index=True, height=220)
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Cancelar", width="stretch"):
                st.session_state["publish_confirm_open"] = False
                st.rerun()
        with col_b:
            if st.button("Publicar ahora", type="primary", width="stretch"):
                try:
                    from domain.cor_client import push_tasks_to_cor
                    with st.spinner("Publicando en ProjectCor…"):
                        result = push_tasks_to_cor(payload)
                    msg = f"Publicación desde '{view}': {result['ok']} OK, {result['fail']} con error."
                    if result["fail"]:
                        fails = [r for r in result["results"] if not r.get("ok")][:5]
                        errors = "\n".join(
                            f"  id={r.get('id')} status={r.get('status')} body={str(r.get('body',''))[:200]}"
                            for r in fails
                        )
                        msg = f"{msg}\nPrimeros errores:\n{errors}"
                    _log(msg)
                except Exception as e:
                    _log(f"ERROR publicando: {e}")
                st.session_state["publish_confirm_open"] = False
                st.rerun()

    _publish_dialog()


# ---------- Tabs ----------

st.markdown(
    """
    <div class="tigo-section">
      <div class="tigo-section__ey">06 · Vista</div>
      <div class="tigo-section__title">Cronograma</div>
      <div class="tigo-section__hint">Cambia de vista, filtra y ajusta fechas</div>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_labels = [
    "Cronograma editable",
    "Gantt (vista)",
    "Disponibilidad",
    "Estadísticas",
    "Notas por país",
]
active_tab = st.segmented_control(
    "Vista",
    options=tab_labels,
    default=st.session_state.get("_active_publish_tab", tab_labels[0]),
    label_visibility="collapsed",
    key="_active_publish_tab",
)
if active_tab is None:
    active_tab = tab_labels[0]

# ---- Cronograma editable (Plan inicial | Plan final) ----
if active_tab == "Cronograma editable":
    kind = st.segmented_control(
        "Plan",
        options=["Plan final", "Plan inicial"],
        default=st.session_state.get("_edit_plan_kind", "Plan final"),
        key="_edit_plan_kind",
    )
    if kind is None:
        kind = "Plan final"
    is_final = kind == "Plan final"

    if is_final:
        pt_view = _filtered_planned_tasks()
        st.caption("Arrastra las barras para cambiar fechas del plan final. Los cambios quedan guardados en sesión.")
    else:
        pt_view = _prepare_aplan_gantt_view(st.session_state["a_plan"])
        st.caption("Arrastra las barras para cambiar fechas del plan inicial. Los cambios quedan guardados en sesión.")

    tasks_payload = _tasks_for_frappe_gantt(pt_view)
    if not tasks_payload:
        st.info("No hay tareas para mostrar con los filtros actuales.")
    else:
        g_key = "gantt_final" if is_final else "gantt_initial"
        change = frappe_gantt(tasks=tasks_payload, view_mode="Week", height=600, key=g_key)
        if isinstance(change, dict) and change.get("type") == "date_change":
            last_key = f"_last_gantt_ts_{g_key}"
            ts = change.get("ts")
            if ts and st.session_state.get(last_key) != ts:
                st.session_state[last_key] = ts
                _apply_date_change(
                    "final" if is_final else "initial",
                    str(change.get("id", "")),
                    str(change.get("start", "")),
                    str(change.get("end", "")),
                )
                st.rerun()

    st.caption("Tabla del cronograma. Haz clic en el nombre de cada columna para filtrar por esa columna.")
    if pt_view.empty:
        st.info("Sincroniza y dibuja el cronograma para ver las tareas aquí.")
    else:
        disp = _build_display_from_planned(pt_view) if is_final else _build_display_from_aplan(pt_view)
        tbl_prefix = "tbl_plan" if is_final else "tbl_aplan"
        pick_key = "pick_task_main" if is_final else "pick_task_aplan"
        filtered = _render_task_table(disp, key_prefix=tbl_prefix)

        if not filtered.empty:
            picked = st.selectbox(
                "Ver detalle de tarea",
                options=[""] + filtered["ID de tarea"].tolist(),
                format_func=lambda v: (
                    "(selecciona una tarea)" if v == ""
                    else f"{v} · {filtered.set_index('ID de tarea').loc[v, 'Tarea'][:60]}"
                    if v in filtered["ID de tarea"].values else v
                ),
                key=pick_key,
            )
            if picked:
                st.session_state["selected_id"] = picked

# ---- Gantt (vista, solo lectura) ----
elif active_tab == "Gantt (vista)":
    view_kind = st.segmented_control(
        "Plan",
        options=["Plan final", "Plan inicial"],
        default=st.session_state.get("_view_plan_kind", "Plan final"),
        key="_view_plan_kind",
    )
    if view_kind is None:
        view_kind = "Plan final"

    if view_kind == "Plan final":
        pt_view = _filtered_planned_tasks()
        if pt_view.empty:
            st.info("Sincroniza y dibuja el cronograma para ver el Gantt.")
        else:
            refs = _load_reference_data()
            email_to_pod = build_email_to_pod(st.session_state["a_plan"], refs["workers_pods"])
            pt_pod = attach_pod_to_planned(pt_view, email_to_pod)
            pt_pod = pt_pod.sort_values(["pod", "resource_name", "start_date", "id"]).reset_index(drop=True)
            st.caption("Vista Gantt del plan final agrupada por cápsula (pod). Solo lectura; edita fechas en **Cronograma editable**.")
            _plot_gantt(pt_pod, key="gantt_view_final")
    else:
        st.caption("Vista Gantt del plan inicial. Solo lectura; edita fechas en **Cronograma editable**.")
        ap_view = _prepare_aplan_gantt_view(st.session_state["a_plan"])
        _plot_gantt(ap_view, key="gantt_view_initial")

# ---- Disponibilidad ----
elif active_tab == "Disponibilidad":
    st.caption("Cada colaborador queda libre al terminar su última tarea (según filtros actuales).")
    pt = _filtered_planned_tasks()
    resources = st.session_state["planned_resources"]

    if not pt.empty and "status" in pt.columns:
        codes_in_data = [c for c in STATUS_DISPLAY.keys() if c in set(pt["status"].dropna().astype(str))]
        labels_in_data = [STATUS_DISPLAY[c] for c in codes_in_data]
        label_to_code = {STATUS_DISPLAY[c]: c for c in codes_in_data}
        picked_labels = st.multiselect(
            "Filtrar por estado",
            labels_in_data,
            default=labels_in_data,
            key="disp_status_filter",
        )
        picked_codes = [label_to_code[lbl] for lbl in picked_labels]
        pt = pt[pt["status"].astype(str).isin(picked_codes)]

    col_pie, col_tbl = st.columns([1, 1])

    with col_pie:
        st.markdown("**Carga pendiente por colaborador**")
        if pt.empty:
            st.info("Sin datos.")
        else:
            pie = (
                pt.assign(dur_h=pd.to_numeric(pt["duration"], errors="coerce").fillna(TASK_FALLBACK_HOURS))
                .groupby("resource_name", dropna=True)["dur_h"]
                .sum()
                .reset_index()
            )
            pie["horas"] = (pie["dur_h"] * WORK_HOURS_RATIO).round(1)
            pie = pie[pie["horas"] > 0].sort_values("horas", ascending=False)
            if pie.empty:
                st.info("No hay horas pendientes.")
            else:
                pie["label"] = _clean_collab_series(pie["resource_name"]) + " · " + pie["horas"].astype(str) + "h"
                fig = px.pie(
                    pie, names="label", values="horas",
                    color_discrete_sequence=BRAND_PALETTE,
                    hole=0.45,
                )
                fig.update_traces(
                    textinfo="percent",
                    textposition="inside",
                    insidetextorientation="radial",
                    hovertemplate="<b>%{label}</b><br>%{percent}<extra></extra>",
                    sort=False,
                )
                fig.update_layout(
                    height=520,
                    margin=dict(l=0, r=0, t=10, b=10),
                    legend=dict(
                        orientation="v", yanchor="middle", y=0.5,
                        xanchor="left", x=1.02, font=dict(size=11),
                    ),
                    uniformtext=dict(minsize=10, mode="hide"),
                )
                st.plotly_chart(fig, key="pie_hours")

    with col_tbl:
        st.markdown("**Detalle por colaborador**")
        if resources.empty:
            st.info("Primero usa 'Dibujar cronograma'.")
        else:
            if pt.empty:
                table = resources.assign(
                    **{
                        "Libre desde": pd.NaT,
                        "Horas pendientes": 0.0,
                        "# Tareas": 0,
                    }
                ).rename(columns={"resource_name": "Colaborador"})[
                    ["Colaborador", "Libre desde", "Horas pendientes", "# Tareas"]
                ]
            else:
                p = pt.copy()
                p["start_dt"] = pd.to_datetime(p["start_date"], errors="coerce")
                p["dur_h"] = pd.to_numeric(p["duration"], errors="coerce").fillna(TASK_FALLBACK_HOURS)
                p["work_h"] = p["dur_h"] * WORK_HOURS_RATIO
                p["end_bh"] = [
                    _bh_end(s, wh) for s, wh in zip(p["start_dt"], p["work_h"])
                ]
                agg = p.groupby(["resource_id", "resource_name"], dropna=False).agg(
                    libre_desde=("end_bh", "max"),
                    horas=("work_h", "sum"),
                    tareas=("id", "count"),
                ).reset_index()
                agg["horas"] = agg["horas"].round(1)
                table = resources.merge(agg, on=["resource_id", "resource_name"], how="left")
                table["libre_desde"] = pd.to_datetime(table["libre_desde"], errors="coerce").dt.strftime("%y-%m-%d %H:%M")
                table["horas"] = table["horas"].fillna(0.0)
                table["tareas"] = table["tareas"].fillna(0).astype(int)
                table = table.rename(columns={
                    "resource_name": "Colaborador",
                    "libre_desde": "Libre desde",
                    "horas": "Horas pend.",
                    "tareas": "# Tareas",
                })[["Colaborador", "Libre desde", "Horas pend.", "# Tareas"]]
            table["Colaborador"] = _clean_collab_series(table["Colaborador"])
            st.markdown("<div class='detail-collab-table'></div>", unsafe_allow_html=True)
            st.dataframe(
                table,
                hide_index=True,
                height=440,
                column_config={
                    "Colaborador": st.column_config.TextColumn(width="small"),
                    "Libre desde": st.column_config.TextColumn(width="small"),
                    "Horas pend.": st.column_config.NumberColumn(width="small", format="%.1f"),
                    "# Tareas": st.column_config.NumberColumn(width="small"),
                },
            )

# ---- Estadísticas ----
elif active_tab == "Estadísticas":
    st.caption("Resumen del plan seleccionado. Se aplican los filtros del panel izquierdo.")

    stats_kind = st.segmented_control(
        "Plan",
        options=["Plan final", "Plan inicial"],
        default=st.session_state.get("_stats_plan_kind", "Plan final"),
        key="_stats_plan_kind",
    )
    if stats_kind is None:
        stats_kind = "Plan final"
    stats_is_final = stats_kind == "Plan final"

    source_df = st.session_state["planned_tasks"] if stats_is_final else st.session_state["a_plan"]
    archived_df = st.session_state.get("archived_tasks", pd.DataFrame())
    include_archived = st.checkbox(
        "Incluir tareas archivadas (finalizadas desde 2026-05-01)",
        value=True,
        key="_stats_include_archived",
        help="Suma las tareas archivadas/finalizadas al análisis. La duración se calcula desde datetime → deadline.",
    )
    if include_archived and archived_df is not None and not archived_df.empty:
        arch = archived_df.copy()
        arch["_is_archived"] = True
        if source_df is None or source_df.empty:
            source_df = arch
        else:
            src = source_df.copy()
            src["_is_archived"] = False
            source_df = pd.concat([src, arch], ignore_index=True, sort=False)

    if source_df is None or source_df.empty:
        st.info("Sincroniza para ver estadísticas.")
    else:
        base = source_df.copy()
        for col in ("tag", "project_name", "skill_names", "skill_main", "typeTask_name"):
            if col not in base.columns:
                base[col] = ""
        for col in ("collab_email_plan", "collab_email"):
            if col not in base.columns:
                base[col] = ""
        if "collab_email_n" not in base.columns:
            base["collab_email_n"] = 0
        ep = base["collab_email_plan"].fillna("").astype(str).str.strip()
        ec = base["collab_email"].fillna("").astype(str).str.strip()
        rid_fallback = ep.where(ep != "", ec).replace("", "SIN_ASIGNAR")
        if "resource_id" not in base.columns or base["resource_id"].isna().all():
            base["resource_id"] = rid_fallback
        else:
            base["resource_id"] = base["resource_id"].fillna(rid_fallback).replace("", "SIN_ASIGNAR")
        base["resource_name"] = base.get("resource_name", base["resource_id"]).fillna(base["resource_id"])
        if "objetivo" not in base.columns:
            base["objetivo"] = detect_business_unit(base["skill_names"])
        if "business_unit" not in base.columns:
            base["business_unit"] = base["project_name"].fillna("").astype(str)
        if "pais" not in base.columns:
            base["pais"] = extract_country_series(base["tag"])
        base["skill_bucket"] = base["skill_main"].fillna("").astype(str)
        base["typeTask_name"] = base["typeTask_name"].fillna("").astype(str)
        base["combo"] = (base["typeTask_name"].replace("", "NA") + " | " + base["skill_bucket"].replace("", "NA"))
        start = pd.to_datetime(base.get("datetime", ""), errors="coerce")
        end = pd.to_datetime(base.get("deadline", ""), errors="coerce")
        dur_from_dates = (end - start).dt.total_seconds() / 3600.0
        if stats_is_final and "duration" in base.columns:
            base["dur_h"] = pd.to_numeric(base["duration"], errors="coerce").fillna(dur_from_dates)
        else:
            base["dur_h"] = dur_from_dates

        df = apply_gantt_filters(
            base,
            resource=st.session_state["filter_resource"],
            countries=st.session_state["filter_country"],
            objective=st.session_state["filter_objective"],
            business_unit=st.session_state["filter_business_unit"],
            date_from=st.session_state["filter_date_from"],
            date_to=st.session_state["filter_date_to"],
        )
        _tt = st.session_state.get("filter_task_type", "Todas")
        if _tt != "Todas" and "typeTask_name" in df.columns:
            _mask = df["typeTask_name"].fillna("").astype(str).str.contains("creativ", case=False, na=False)
            df = df[_mask] if _tt == "Creatividad" else df[~_mask]

        if "status" in df.columns:
            status_codes_present = [
                c for c in STATUS_DISPLAY.keys()
                if c in set(df["status"].dropna().astype(str))
            ]
            seen = set()
            status_codes_present = [c for c in status_codes_present if not (c in seen or seen.add(c))]
            status_labels = [STATUS_DISPLAY.get(c, c) for c in status_codes_present]
            label_to_code = {STATUS_DISPLAY.get(c, c): c for c in status_codes_present}
            if status_labels:
                default_labels = st.session_state.get("_stats_status_filter", status_labels)
                default_labels = [l for l in default_labels if l in status_labels] or status_labels
                pills_fn = getattr(st, "pills", None)
                if callable(pills_fn):
                    sel_labels = pills_fn(
                        "Estados incluidos (click para ocultar)",
                        options=status_labels,
                        default=default_labels,
                        selection_mode="multi",
                        key="_stats_status_filter",
                    )
                else:
                    sel_labels = st.multiselect(
                        "Estados incluidos (quita para ocultar)",
                        options=status_labels,
                        default=default_labels,
                        key="_stats_status_filter",
                    )
                if sel_labels is None:
                    sel_labels = []
                sel_codes = {label_to_code[l] for l in sel_labels if l in label_to_code}
                if sel_codes:
                    df = df[df["status"].astype(str).isin(sel_codes)]
                else:
                    df = df.iloc[0:0]

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Tareas por país**")
            d = df.copy()
            d["pais"] = d["pais"].replace("", "SIN PAÍS")
            counts = d.groupby("pais").size().reset_index(name="n").sort_values("n", ascending=False)
            if counts.empty:
                st.info("Sin datos.")
            else:
                fig = px.bar(counts, x="pais", y="n", color_discrete_sequence=["#001eb4"])
                fig.update_layout(height=380, xaxis_title="País", yaxis_title="# tareas", margin=dict(l=0, r=0, t=10, b=40))
                st.plotly_chart(fig, key="stats_country")

        with col2:
            st.markdown("**Tareas por tipo + skill**")
            d = df.copy()
            d["combo"] = d["combo"].replace({"NA | NA": "SIN CLASIFICAR", "": "SIN CLASIFICAR"})
            counts = d.groupby("combo").size().reset_index(name="n").sort_values("n", ascending=True)
            if counts.empty:
                st.info("Sin datos.")
            else:
                fig = px.bar(counts, x="n", y="combo", orientation="h", color_discrete_sequence=["#44c8f5"])
                fig.update_layout(height=380, xaxis_title="# tareas", yaxis_title="", margin=dict(l=0, r=0, t=10, b=10))
                st.plotly_chart(fig, key="stats_combo")

        col3, col4 = st.columns(2)
        with col3:
            st.markdown("**Distribución de # de correos por tarea**")
            d = df.copy()
            d["n_emails"] = pd.to_numeric(d["collab_email_n"], errors="coerce").fillna(0).astype(int)
            counts = d.groupby("n_emails").size().reset_index(name="n").sort_values("n_emails")
            if counts.empty:
                st.info("Sin datos.")
            else:
                counts["label"] = counts["n_emails"].astype(str) + " correo(s)"
                fig = px.pie(counts, names="label", values="n", color_discrete_sequence=BRAND_PALETTE)
                fig.update_traces(textinfo="label+percent")
                fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=10))
                st.plotly_chart(fig, key="stats_emails")

        with col4:
            st.markdown("**Distribución de duración (días) por tipo + skill**")
            d = df.copy()
            d["combo"] = d["combo"].replace({"NA | NA": "SIN CLASIFICAR", "": "SIN CLASIFICAR"})
            d = d[d["dur_h"].notna() & (d["dur_h"] > 0)]
            if d.empty:
                st.info("Sin datos.")
            else:
                d["dur_d"] = d["dur_h"] / 24.0
                order = d.groupby("combo")["dur_d"].median().sort_values(ascending=False).index.tolist()
                name_col = "title" if "title" in d.columns else ("text" if "text" in d.columns else None)
                fig = px.box(
                    d, x="dur_d", y="combo",
                    color="combo",
                    color_discrete_sequence=BRAND_PALETTE,
                    category_orders={"combo": order},
                    points="outliers",
                    orientation="h",
                    custom_data=[name_col] if name_col else None,
                )
                if name_col:
                    fig.update_traces(
                        hovertemplate="Tarea: %{customdata[0]}<br>Combo: %{y}<br>Duración: %{x:.2f} días<extra></extra>"
                    )
                fig.update_layout(
                    height=max(380, 28 * len(order) + 80),
                    xaxis_title="Duración (días)", yaxis_title="",
                    margin=dict(l=0, r=0, t=10, b=10),
                    showlegend=False,
                )
                st.plotly_chart(fig, key="stats_duration")

# ---- Notas por país ----
elif active_tab == "Notas por país":
    country_choices = build_country_choices(st.session_state["planned_tasks"], st.session_state["comments_df"])
    labels_to_code = country_choices
    code_options = list(labels_to_code.values())

    col_form, col_list = st.columns([1, 2])

    with col_form:
        st.markdown("**Nueva nota**")
        with st.form("new_comment", clear_on_submit=False):
            c_date = st.date_input("Fecha", value=dt.date.today())
            c_country = st.selectbox(
                "País",
                options=code_options,
                format_func=lambda code: next((lbl for lbl, c in labels_to_code.items() if c == code), code),
                key="new_comment_country",
            )
            c_text = st.text_area("Nota", placeholder="Escribe aquí la nota…", height=140, key="new_comment_text")
            submitted = st.form_submit_button("Guardar nota", type="primary", icon=":material/save:")

        if submitted:
            country_code = (c_country or "").strip().upper()
            text = (c_text or "").strip()
            if not country_code or country_code not in code_options:
                st.warning("Debes seleccionar una etiqueta de país válida.")
            elif not text:
                st.warning("Escribe un comentario antes de guardar.")
            else:
                new_row = pd.DataFrame([{
                    "saved_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "comment_date": c_date.isoformat(),
                    "country": country_code,
                    "comment": text,
                }], columns=COMMENT_COLS)
                current = st.session_state["comments_df"]
                merged = pd.concat([current, new_row], ignore_index=True)
                path = st.session_state["comments_path"]
                if path is None or not save_comments_file(merged, path):
                    st.error("No se pudo guardar la nota.")
                else:
                    st.session_state["comments_df"] = load_comments_file(path)
                    st.success("Nota guardada.")
                    st.rerun()

    with col_list:
        st.markdown("**Historial de notas**")
        st.caption("La lista se filtra por los países seleccionados en el panel izquierdo.")
        comments = st.session_state["comments_df"].copy()
        if comments.empty:
            st.info("Aún no hay notas guardadas.")
        else:
            comments["comment_date"] = pd.to_datetime(comments["comment_date"], errors="coerce").dt.date
            comments["country"] = comments["country"].fillna("").astype(str).str.strip().str.upper()
            sel = st.session_state["filter_country"]
            if sel and ALL_SENTINEL not in sel:
                comments = comments[comments["country"].isin([s.upper() for s in sel])]
            comments = comments.sort_values(["comment_date", "saved_at"], ascending=[False, False])
            if comments.empty:
                st.info("No hay notas para el filtro de país actual.")
            else:
                display = comments.rename(columns={
                    "comment_date": "Fecha",
                    "country": "País",
                    "comment": "Comentario",
                    "saved_at": "Guardado en",
                })
                st.dataframe(display[["Fecha", "País", "Comentario", "Guardado en"]], hide_index=True, height=420)


# ---------- Log ----------

st.markdown(
    f"""
    <div class="tigo-section" style="margin-top:20px;">
      <div class="tigo-section__ey">Sistema</div>
      <div class="tigo-section__title">Log</div>
      <div class="tigo-section__hint">Estado de la última acción</div>
    </div>
    <div class="logbox">{(st.session_state["log"] or "").replace("<", "&lt;")}</div>
    """,
    unsafe_allow_html=True,
)
