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
    TASK_FALLBACK_HOURS,
    TZ_LOCAL,
    WORK_HOURS_RATIO,
)
from domain.country import build_country_choices, extract_country_series
from domain.data_loader import load_sla, load_workers_allowed, load_workers_pods
from domain.filters import apply_gantt_filters
from domain.pack import pack_all_resources_no_gaps
from domain.pods import attach_pod_to_planned, build_email_to_pod
from components.frappe_gantt import frappe_gantt
from domain.payload import (
    build_planned_from_a_plan,
    build_push_payload_editable,
    build_push_payload_original,
    detect_business_unit,
)

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"

st.set_page_config(
    page_title="Planificador de diseño",
    page_icon=":material/schedule:",
    layout="wide",
)

# ---------- Session state ----------

def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("planned_tasks", pd.DataFrame())
    ss.setdefault("planned_resources", pd.DataFrame(columns=["resource_id", "resource_name"]))
    ss.setdefault("a_plan", pd.DataFrame())
    ss.setdefault("a_plan_baseline", pd.DataFrame(columns=["id", "datetime", "deadline"]))
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
    ss.setdefault("filter_business_unit", ALL_SENTINEL)


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
    return apply_gantt_filters(
        st.session_state["planned_tasks"],
        resource=st.session_state["filter_resource"],
        countries=st.session_state["filter_country"],
        objective=st.session_state["filter_objective"],
        business_unit=st.session_state["filter_business_unit"],
    )


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
    )
    return filtered.sort_values(["resource_id", "start_date", "id"]).reset_index(drop=True)


def _to_timeline_df(tasks: pd.DataFrame) -> pd.DataFrame:
    if tasks is None or tasks.empty:
        return pd.DataFrame()
    df = tasks.copy()
    df["_start"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["_dur"] = pd.to_numeric(df["duration"], errors="coerce").fillna(TASK_FALLBACK_HOURS)
    df["_end"] = df["_start"] + pd.to_timedelta(df["_dur"], unit="h")
    df = df[df["_start"].notna()]
    df["Estado"] = df.get("status", "").fillna("").astype(str)
    df["Tarea"] = df.get("text", "").fillna("").astype(str)
    df["Colaborador"] = df["resource_name"].fillna(df["resource_id"]).astype(str)
    return df


def _plot_gantt(tasks: pd.DataFrame, key: str) -> None:
    df = _to_timeline_df(tasks)
    if df.empty:
        st.info("No hay tareas para mostrar con los filtros actuales.")
        return
    color_map = {**STATUS_COLORS, "": DEFAULT_STATUS_COLOR}
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


# ---------- Global styles (Tigo Design System v2) ----------

ASSETS_DIR = APP_DIR / "assets"


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

    st.markdown("")

    def _do_sync():
        try:
            from domain.cor_client import build_a_from_cor
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

                a = build_a_from_cor(workers_df=refs["workers_pods"], progress_cb=_on_progress)
                progress_msg.write("Calculando SLA y cronograma…")
                progress_bar.progress(100)
                a = add_sla_to_a(a, refs["sla"])
                res = schedule_tasks_from_today(a, refs["workers_allowed"])
                status_box.update(label=f"Sincronización lista · {len(res['a_plan'])} tareas", state="complete")
            ap_new = res["a_plan"]
            st.session_state["a_plan"] = ap_new
            if not ap_new.empty:
                st.session_state["a_plan_baseline"] = ap_new[["id", "datetime", "deadline"]].assign(id=lambda d: d["id"].astype(str))
            else:
                st.session_state["a_plan_baseline"] = pd.DataFrame(columns=["id", "datetime", "deadline"])
            _log(f"Sincronizado con ProjectCor. Tareas: {len(ap_new)}\nSiguiente paso: dibujar el cronograma.")
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
                    ("Estado", r.get("status", "")),
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
    "Gantt interactivo",
    "Plan original",
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

# ---- Cronograma editable ----
if active_tab == "Cronograma editable":
    pt_view = _filtered_planned_tasks()
    _plot_gantt(pt_view, key="gantt_main")

    st.caption("Ajusta fechas, duración o colaborador en la tabla. Marca 'Eliminar' para quitar la tarea del cronograma (vuelve al sincronizar).")
    if pt_view.empty:
        st.info("Sincroniza y dibuja el cronograma para editar tareas aquí.")
    else:
        edit_df = pt_view[[
            "id", "text", "resource_id", "start_date", "duration", "pais", "objetivo", "status",
        ]].copy()
        edit_df["Eliminar"] = False
        edit_df["duration"] = pd.to_numeric(edit_df["duration"], errors="coerce").fillna(TASK_FALLBACK_HOURS).astype(float)

        edited = st.data_editor(
            edit_df,
            hide_index=True,
            key="editor_main",
            num_rows="fixed",
            column_config={
                "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
                "text": st.column_config.TextColumn("Tarea", disabled=True, width="medium"),
                "resource_id": st.column_config.TextColumn("Colaborador (email)"),
                "start_date": st.column_config.TextColumn("Inicio (YYYY-MM-DD HH:MM)"),
                "duration": st.column_config.NumberColumn("Duración (h)", min_value=0.25, step=0.25, format="%.2f"),
                "pais": st.column_config.TextColumn("País", disabled=True, width="small"),
                "objetivo": st.column_config.TextColumn("Objetivo", disabled=True),
                "status": st.column_config.TextColumn("Estado", disabled=True, width="small"),
                "Eliminar": st.column_config.CheckboxColumn("Quitar"),
            },
        )

        col_apply, col_pick = st.columns([1, 3])
        with col_apply:
            if st.button("Aplicar cambios", icon=":material/save:"):
                original = st.session_state["planned_tasks"].copy()
                original["id"] = original["id"].astype(str)
                edited["id"] = edited["id"].astype(str)

                to_drop_ids = set(edited.loc[edited["Eliminar"] == True, "id"])
                edits = edited[edited["Eliminar"] != True].set_index("id")

                merged = original.set_index("id")
                for col in ("resource_id", "start_date", "duration"):
                    merged.loc[edits.index, col] = edits[col].values
                merged = merged[~merged.index.isin(to_drop_ids)].reset_index()

                resources = st.session_state["planned_resources"]
                if not resources.empty:
                    merged = merged.drop(columns=["resource_name"], errors="ignore").merge(
                        resources, on="resource_id", how="left"
                    )
                    merged["resource_name"] = merged["resource_name"].fillna(merged["resource_id"])

                st.session_state["planned_tasks"] = merged
                _log(
                    f"Cambios aplicados. Tareas quitadas: {len(to_drop_ids)}. "
                    "Usa 'Publicar' para enviar a ProjectCor o 'Compactar' para eliminar huecos."
                )
                st.rerun()
        with col_pick:
            picked = st.selectbox(
                "Ver detalle de tarea",
                options=[""] + edit_df["id"].tolist(),
                format_func=lambda v: "(selecciona una tarea)" if v == "" else f"{v} · {edit_df.set_index('id').loc[v, 'text'][:60]}" if v in edit_df['id'].values else v,
                key="pick_task_main",
            )
            if picked:
                st.session_state["selected_id"] = picked

# ---- Gantt interactivo (Frappe) ----
elif active_tab == "Gantt interactivo":
    pt_view = _filtered_planned_tasks()
    if pt_view.empty:
        st.info("Sincroniza y dibuja el cronograma para ver el Gantt interactivo.")
    else:
        refs = _load_reference_data()
        email_to_pod = build_email_to_pod(st.session_state["a_plan"], refs["workers_pods"])
        pt_pod = attach_pod_to_planned(pt_view, email_to_pod)
        pt_pod = pt_pod.sort_values(["pod", "resource_name", "start_date", "id"]).reset_index(drop=True)

        col_view, col_hint = st.columns([1, 3])
        with col_view:
            view_mode = st.selectbox(
                "Escala",
                options=["Day", "Week", "Month"],
                index=1,
                key="frappe_view_mode",
            )
        with col_hint:
            st.caption(
                "Arrastra las barras para mover fechas · redimensiona los bordes para cambiar duración. "
                "Los cambios se aplican automáticamente al plan en memoria; usa **Publicar** para enviarlos a ProjectCor."
            )

        start_dt = pd.to_datetime(pt_pod["start_date"], errors="coerce")
        dur = pd.to_numeric(pt_pod["duration"], errors="coerce").fillna(TASK_FALLBACK_HOURS)
        end_dt = start_dt + pd.to_timedelta(dur, unit="h")

        tasks_payload = []
        for _, row in pt_pod.assign(_start=start_dt, _end=end_dt).iterrows():
            if pd.isna(row["_start"]) or pd.isna(row["_end"]):
                continue
            title = str(row.get("text") or "")[:80]
            resource = str(row.get("resource_name") or row.get("resource_id") or "")
            pod = str(row.get("pod") or "Sin pod")
            tasks_payload.append({
                "id": str(row["id"]),
                "name": f"[{pod} · {resource.split('@')[0]}] {title}",
                "start": row["_start"].strftime("%Y-%m-%d"),
                "end": row["_end"].strftime("%Y-%m-%d"),
                "progress": 0,
                "custom_class": f"pod-{pod.replace(' ', '_')}",
            })

        boundaries: list[int] = []
        prev_pod = None
        for i, row in pt_pod.iterrows():
            cur = row.get("pod") or "Sin pod"
            if prev_pod is not None and cur != prev_pod:
                boundaries.append(i)
            prev_pod = cur

        drag_event = frappe_gantt(
            tasks=tasks_payload,
            view_mode=view_mode,
            pod_boundaries=boundaries,
            key="frappe_gantt_main",
        )

        last_ts = st.session_state.get("_frappe_last_ts", 0)
        if drag_event and drag_event.get("type") == "date_change" and drag_event.get("ts", 0) > last_ts:
            st.session_state["_frappe_last_ts"] = drag_event["ts"]
            tid = str(drag_event["id"])
            new_start = pd.to_datetime(drag_event["start"], errors="coerce")
            new_end = pd.to_datetime(drag_event["end"], errors="coerce")
            if pd.notna(new_start) and pd.notna(new_end):
                pt_all = st.session_state["planned_tasks"].copy()
                pt_all["id"] = pt_all["id"].astype(str)
                mask = pt_all["id"] == tid
                if mask.any():
                    pt_all.loc[mask, "start_date"] = new_start.strftime("%Y-%m-%d %H:%M")
                    new_dur = max(0.25, (new_end - new_start).total_seconds() / 3600.0)
                    pt_all.loc[mask, "duration"] = float(new_dur)
                    st.session_state["planned_tasks"] = pt_all
                    _log(f"Frappe drag: tarea {tid} → {new_start:%Y-%m-%d} ({new_dur:.1f}h)")
                    st.rerun()

# ---- Plan original ----
elif active_tab == "Plan original":
    st.caption(
        "Asignación inicial entregada por el algoritmo. Puedes ajustar fechas aquí y publicarlas tal cual en ProjectCor."
    )
    ap_view = _prepare_aplan_gantt_view(st.session_state["a_plan"])
    _plot_gantt(ap_view, key="gantt_aplan")

    ap_now = st.session_state["a_plan"]
    if ap_now is None or ap_now.empty:
        st.info("Sincroniza para cargar el plan original.")
    else:
        visible_ids = set(ap_view["id"].astype(str)) if not ap_view.empty else set()
        editable = ap_now[ap_now["id"].astype(str).isin(visible_ids)].copy() if visible_ids else pd.DataFrame(columns=ap_now.columns)
        if editable.empty:
            st.info("No hay tareas visibles con los filtros actuales.")
        else:
            editable["id"] = editable["id"].astype(str)
            editable["title"] = editable.get("title", "").fillna("").astype(str)
            editable["collab_email_plan"] = editable.get("collab_email_plan", "").fillna("").astype(str)
            editable["datetime"] = editable["datetime"].astype(str)
            editable["deadline"] = editable["deadline"].astype(str)
            edit_df = editable[["id", "title", "collab_email_plan", "datetime", "deadline"]].copy()

            edited = st.data_editor(
                edit_df,
                hide_index=True,
                key="editor_aplan",
                num_rows="fixed",
                column_config={
                    "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
                    "title": st.column_config.TextColumn("Tarea", disabled=True),
                    "collab_email_plan": st.column_config.TextColumn("Colaborador (email)"),
                    "datetime": st.column_config.TextColumn("Inicio (YYYY-MM-DD HH:MM:SS)"),
                    "deadline": st.column_config.TextColumn("Deadline (YYYY-MM-DD HH:MM:SS)"),
                },
            )

            if st.button("Aplicar cambios en Plan original", icon=":material/save:"):
                orig = st.session_state["a_plan"].copy()
                orig["id"] = orig["id"].astype(str)
                edited["id"] = edited["id"].astype(str)
                indexed = edited.set_index("id")
                merged = orig.set_index("id")
                for col in ("collab_email_plan", "datetime", "deadline"):
                    merged.loc[indexed.index, col] = indexed[col].values
                st.session_state["a_plan"] = merged.reset_index()
                _log("Plan original actualizado. Usa 'Publicar' para enviar los cambios a ProjectCor.")
                st.rerun()

# ---- Disponibilidad ----
elif active_tab == "Disponibilidad":
    st.caption("Cada colaborador queda libre al terminar su última tarea (según filtros actuales).")
    pt = _filtered_planned_tasks()
    resources = st.session_state["planned_resources"]

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
                pie["label"] = pie["resource_name"] + " (" + pie["horas"].astype(str) + "h)"
                fig = px.pie(
                    pie, names="label", values="horas",
                    color_discrete_sequence=BRAND_PALETTE,
                )
                fig.update_traces(textinfo="label+percent")
                fig.update_layout(height=440, margin=dict(l=0, r=0, t=10, b=10))
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
                p["end_dt"] = p["start_dt"] + pd.to_timedelta(p["dur_h"], unit="h")
                agg = p.groupby(["resource_id", "resource_name"], dropna=False).agg(
                    libre_desde=("end_dt", "max"),
                    horas=("dur_h", lambda s: (s.sum() * WORK_HOURS_RATIO)),
                    tareas=("id", "count"),
                ).reset_index()
                agg["horas"] = agg["horas"].round(1)
                table = resources.merge(agg, on=["resource_id", "resource_name"], how="left")
                table["libre_desde"] = table["libre_desde"].dt.strftime("%Y-%m-%d %H:%M")
                table["horas"] = table["horas"].fillna(0.0)
                table["tareas"] = table["tareas"].fillna(0).astype(int)
                table = table.rename(columns={
                    "resource_name": "Colaborador",
                    "libre_desde": "Libre desde",
                    "horas": "Horas pendientes",
                    "tareas": "# Tareas",
                })[["Colaborador", "Libre desde", "Horas pendientes", "# Tareas"]]
            st.dataframe(table, hide_index=True, height=440)

# ---- Estadísticas ----
elif active_tab == "Estadísticas":
    st.caption("Resumen del plan actual. Se aplican los filtros del panel izquierdo.")
    ap_now = st.session_state["a_plan"]
    if ap_now is None or ap_now.empty:
        st.info("Sincroniza para ver estadísticas.")
    else:
        base = ap_now.copy()
        for col in ("tag", "project_name", "skill_names", "skill_main", "typeTask_name", "datetime", "deadline"):
            if col not in base.columns:
                base[col] = ""
        for col in ("collab_email_plan", "collab_email"):
            if col not in base.columns:
                base[col] = ""
        if "collab_email_n" not in base.columns:
            base["collab_email_n"] = 0
        ep = base["collab_email_plan"].fillna("").astype(str).str.strip()
        ec = base["collab_email"].fillna("").astype(str).str.strip()
        base["resource_id"] = ep.where(ep != "", ec).replace("", "SIN_ASIGNAR")
        base["resource_name"] = base["resource_id"]
        base["objetivo"] = detect_business_unit(base["skill_names"])
        base["business_unit"] = base["project_name"].fillna("").astype(str)
        base["pais"] = extract_country_series(base["tag"])
        base["skill_bucket"] = base["skill_main"].fillna("").astype(str)
        base["typeTask_name"] = base["typeTask_name"].fillna("").astype(str)
        base["combo"] = (base["typeTask_name"].replace("", "NA") + " | " + base["skill_bucket"].replace("", "NA"))
        start = pd.to_datetime(base["datetime"], errors="coerce")
        end = pd.to_datetime(base["deadline"], errors="coerce")
        base["dur_h"] = (end - start).dt.total_seconds() / 3600.0

        df = apply_gantt_filters(
            base,
            resource=st.session_state["filter_resource"],
            countries=st.session_state["filter_country"],
            objective=st.session_state["filter_objective"],
            business_unit=st.session_state["filter_business_unit"],
        )

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
            st.markdown("**Duración (h) por tipo + skill**")
            d = df.copy()
            d["combo"] = d["combo"].replace({"NA | NA": "SIN CLASIFICAR", "": "SIN CLASIFICAR"})
            d = d[d["dur_h"].notna() & (d["dur_h"] > 0)]
            if d.empty:
                st.info("Sin datos.")
            else:
                fig = px.histogram(d, x="dur_h", color="combo", color_discrete_sequence=BRAND_PALETTE, barmode="overlay")
                fig.update_layout(height=380, xaxis_title="Duración (h)", yaxis_title="# tareas", margin=dict(l=0, r=0, t=10, b=10))
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
