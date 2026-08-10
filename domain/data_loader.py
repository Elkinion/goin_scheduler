from __future__ import annotations

from pathlib import Path

import pandas as pd


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).rstrip(";").strip() for c in df.columns]
    for col in df.columns:
        try:
            df[col] = df[col].astype(str).str.rstrip(";").str.strip()
        except Exception:
            pass
    return df.dropna(axis=1, how="all")


def load_workers_allowed(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return _clean_frame(df)[["collab_email", "allowed_code"]]


def load_workers_pods(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return _clean_frame(df)


def load_sla(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, keep_default_na=False)
    df = _clean_frame(df)
    for col in ("tiempo_dias", "cantidad_por_dia"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
