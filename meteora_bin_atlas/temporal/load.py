"""Load temporal artifacts written by the TypeScript fetch pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from meteora_bin_atlas.paths import DATA_PROCESSED, DATA_SIMULATED, latest_matching


def load_pool_ohlcv(
    pool_address: str,
    *,
    timeframe: str | None = None,
    processed_dir: Path = DATA_PROCESSED,
) -> tuple[pd.DataFrame, dict]:
    """Load the latest OHLCV JSON for a pool and return candles + metadata."""
    pattern = (
        f"pool_ohlcv_{pool_address}_{timeframe}_*.json"
        if timeframe
        else f"pool_ohlcv_{pool_address}_*.json"
    )
    path = latest_matching(processed_dir, pattern)
    with path.open() as f:
        payload = json.load(f)

    df = pd.DataFrame(payload["data"])
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)

    meta = {
        "path": path,
        "pool_address": payload["pool_address"],
        "timeframe": payload["timeframe"],
        "fetched_at_utc": payload["fetched_at_utc"],
        "candle_count": len(df),
    }
    return df, meta


def load_snapshot_series_manifest(
    pool_address: str,
    *,
    processed_dir: Path = DATA_PROCESSED,
) -> tuple[dict, Path]:
    """Load the latest snapshot-series manifest JSON for a pool."""
    path = latest_matching(processed_dir, f"snapshot_series_{pool_address}_*.json")
    with path.open() as f:
        manifest = json.load(f)
    return manifest, path


def _format_timestamp_for_filename(iso_utc: str) -> str:
    """Match TypeScript ``formatTimestampForFilename`` used for series CSV stems."""
    return iso_utc.replace(":", "-").replace(".", "-")


def _read_bin_atlas_series_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "fetched_at_utc" in df.columns:
        df["fetched_at_utc"] = pd.to_datetime(df["fetched_at_utc"], utc=True)
    return df


def load_real_bin_atlas_series(
    pool_address: str,
    *,
    processed_dir: Path = DATA_PROCESSED,
) -> tuple[pd.DataFrame, Path]:
    """Load the latest RPC-fetched series CSV linked to a snapshot_series manifest."""
    manifest, manifest_path = load_snapshot_series_manifest(pool_address, processed_dir=processed_dir)
    stamp = _format_timestamp_for_filename(manifest["series_completed_at_utc"])
    path = processed_dir / f"bin_atlas_series_{pool_address}_{stamp}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Real series CSV not found for manifest {manifest_path.name}: {path}"
        )
    return _read_bin_atlas_series_csv(path), path


def load_bin_atlas_series(
    pool_address: str,
    *,
    processed_dir: Path = DATA_PROCESSED,
) -> tuple[pd.DataFrame, Path]:
    """Load the latest real bin-atlas series CSV for a pool from ``data/processed``."""
    return load_real_bin_atlas_series(pool_address, processed_dir=processed_dir)


def load_simulated_bin_atlas_series(
    pool_address: str,
    *,
    simulated_dir: Path = DATA_SIMULATED,
) -> tuple[pd.DataFrame, Path]:
    """Load the latest simulated bin-atlas series CSV for a pool from ``data/simulated``."""
    path = latest_matching(simulated_dir, f"bin_atlas_series_{pool_address}_*.csv")
    return _read_bin_atlas_series_csv(path), path


def summarize_bin_atlas_series(df: pd.DataFrame) -> pd.DataFrame:
    """Per-snapshot summary: active bin, liquidity totals, time span."""
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["liquidity_num"] = pd.to_numeric(work["liquidity"], errors="coerce")

    summaries: list[dict] = []
    for snapshot_index, group in work.groupby("snapshot_index"):
        active = group.loc[group["is_active_bin"] == True]  # noqa: E712
        summaries.append(
            {
                "snapshot_index": snapshot_index,
                "fetched_at_utc": group["fetched_at_utc"].iloc[0],
                "active_bin_id": active["bin_id"].iloc[0] if not active.empty else pd.NA,
                "bin_count": len(group),
                "liquidity_total": group["liquidity_num"].sum(),
                "bins_with_liquidity": (group["liquidity_num"] > 0).sum(),
            }
        )

    return pd.DataFrame(summaries).sort_values("snapshot_index").reset_index(drop=True)
