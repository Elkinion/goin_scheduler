import os
from zoneinfo import ZoneInfo

TZ_LOCAL = ZoneInfo("America/Bogota")
API_TZ = ZoneInfo("UTC")

TASK_FALLBACK_HOURS = 9

WORK_HOURS_PER_DAY = 8
SCHED_HOURS_PER_DAY = 24
WORK_HOURS_RATIO = WORK_HOURS_PER_DAY / SCHED_HOURS_PER_DAY

DAY_START_HOUR = 9
COUNTRY_CODES = ("GT", "CR", "NI", "PA", "SV", "HN")
WORK_ROLES = ("Designer 1", "Designer 2", "Multimedia Producer")

API_BASE = "https://api.projectcor.com/v1"


def _load_secret(name: str, default: str) -> str:
    val = os.environ.get(name)
    if val:
        return val
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default


COR_API_KEY = _load_secret("COR_API_KEY", "5c7f823b-e5c1-48c5-ac1a-51709ae42571")
COR_CLIENT_SECRET = _load_secret("COR_CLIENT_SECRET", "bfb89cd972de39f679be2636b4c5e5de")

ALL_SENTINEL = "__ALL__"

# Tigo Design System v2 tokens
TIGO_BLUE_500 = "#001EB4"
TIGO_BLUE_400 = "#0026E5"
TIGO_BLUE_900 = "#00005A"
TIGO_BLUE_50 = "#E6F1FF"
TIGO_CYAN = "#44C8F5"
TIGO_YELLOW = "#FFBE00"
TIGO_GREEN = "#00F52D"
TIGO_MAGENTA = "#FF0064"
TIGO_ORANGE = "#FB561E"
TIGO_LIME = "#BEFF00"
TIGO_WA = "#25D366"
TIGO_ERR = "#C62828"
TIGO_OK = "#1A7F3C"

STATUS_COLORS = {
    "nueva": TIGO_CYAN,
    "en_proceso": TIGO_YELLOW,
    "en_revision": "#9B5AB9",
    "ajustes": TIGO_MAGENTA,
    "suspendida": "#909090",
    "finalizada": TIGO_GREEN,
}
DEFAULT_STATUS_COLOR = "#9E9E9E"

STATUS_DISPLAY = {
    "nueva": "Nueva",
    "en_proceso": "En proceso",
    "en_revision": "En revisión",
    "ajustes": "Ajustes",
    "suspendida": "Suspendida",
    "finalizada": "Finalizada",
    # Fallback: si algún estado llega sin normalizar desde la API
    "en_diseno": "Ajustes",
    "estancada": "Suspendida",
}

BRAND_PALETTE = [
    TIGO_BLUE_500, TIGO_CYAN, TIGO_YELLOW, TIGO_GREEN,
    TIGO_MAGENTA, TIGO_ORANGE, TIGO_BLUE_900, TIGO_LIME,
]
