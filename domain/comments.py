from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

COMMENT_COLS = ["saved_at", "comment_date", "country", "comment"]


def _can_write_dir(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def resolve_comments_dir(base_dir: Path) -> tuple[Path, list[Path]]:
    preferred = os.environ.get("COMMENTS_DATA_DIR", "").strip()
    candidates: list[Path] = []
    if preferred:
        candidates.append(Path(preferred))
    candidates.append(base_dir / "data")
    candidates.append(Path.home() / ".goin_comments")
    candidates.append(Path(os.environ.get("TEMP", "/tmp")) / "goin_comments")

    seen: set[str] = set()
    uniq: list[Path] = []
    for c in candidates:
        s = str(c)
        if s and s not in seen:
            seen.add(s)
            uniq.append(c)

    for d in uniq:
        if _can_write_dir(d):
            return d, uniq
    raise RuntimeError("No hay una carpeta escribible para comentarios.")


def empty_comments_df() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in COMMENT_COLS})


def load_comments_file(path: Path) -> pd.DataFrame:
    if not path.exists():
        return empty_comments_df()
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    except Exception:
        return empty_comments_df()
    for c in COMMENT_COLS:
        if c not in df.columns:
            df[c] = ""
    return df[COMMENT_COLS]


def save_comments_file(df: pd.DataFrame, path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, encoding="utf-8")
        return True
    except Exception:
        return False
