"""Project paths and filesystem helpers."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def resolve_project_root(start: Path | None = None) -> Path:
    """Find the repo root by walking up until ``pyproject.toml`` is found."""
    start = (start or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise FileNotFoundError("Could not find project root (pyproject.toml)")


PROJECT_ROOT = resolve_project_root(Path(__file__).resolve().parent)
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
PLOTS_DIR = PROJECT_ROOT / "plots"


def load_project_env() -> None:
    """Load ``.env`` from the project root if present."""
    load_dotenv(PROJECT_ROOT / ".env")


def latest_matching(directory: Path, pattern: str) -> Path:
    """Return the lexicographically last path matching ``pattern`` in ``directory``."""
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No files matching {pattern} in {directory}")
    return matches[-1]


def all_matching(directory: Path, pattern: str) -> list[Path]:
    """Return all paths matching ``pattern`` in ``directory``, sorted."""
    return sorted(directory.glob(pattern))
