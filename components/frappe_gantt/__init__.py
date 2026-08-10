from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

_FRONTEND_DIR = Path(__file__).parent / "frontend"
_component = components.declare_component("frappe_gantt", path=str(_FRONTEND_DIR))


def frappe_gantt(
    tasks: list[dict],
    view_mode: str = "Week",
    pod_boundaries: list[int] | None = None,
    height: int = 600,
    key: str | None = None,
) -> Any:
    return _component(
        tasks=tasks,
        view_mode=view_mode,
        pod_boundaries=pod_boundaries or [],
        height=height,
        key=key,
        default=None,
    )
