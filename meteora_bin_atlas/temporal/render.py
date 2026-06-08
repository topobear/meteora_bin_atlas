"""Render temporal bin-atlas snapshots to MP4."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from meteora_bin_atlas.config import DEFAULT_POOL_ADDRESS, get_pool_address
from meteora_bin_atlas.explore.labels import parse_token_labels
from meteora_bin_atlas.paths import DATA_PROCESSED, PLOTS_DIR
from meteora_bin_atlas.temporal.load import load_bin_atlas_series
from meteora_bin_atlas.temporal.seismic import (
    encode_mp4,
    prepare_snapshot_traces,
    render_seismic_frame,
)


def resolve_token_labels(pool_address: str, processed_dir: Path) -> tuple[str, str]:
    candidates_path = processed_dir / "pool_candidates.csv"
    if candidates_path.exists():
        candidates = pd.read_csv(candidates_path)
        match = candidates[candidates["pool_address"] == pool_address]
        if not match.empty:
            label_col = "label" if "label" in match.columns else "pool_label"
            pool_label = match.iloc[0].get(label_col, pool_address)
            tokens = parse_token_labels(str(pool_label), pool_address)
            return tokens.token_x, tokens.token_y

    tokens = parse_token_labels("SOL-USDC", pool_address)
    return tokens.token_x, tokens.token_y


def build_temporal_mp4(
    pool_address: str | None = None,
    *,
    output_path: Path | None = None,
    series_csv: Path | None = None,
    frame_duration_sec: float = 1.0,
    fps: int = 10,
    zoom_bins: int = 30,
    processed_dir: Path = DATA_PROCESSED,
    dpi: int = 150,
) -> Path:
    """Build a seismic-style MP4 from the latest bin-atlas series CSV for a pool."""
    pool_address = pool_address or get_pool_address()

    if series_csv is not None:
        series_df = pd.read_csv(series_csv)
        if "fetched_at_utc" in series_df.columns:
            series_df["fetched_at_utc"] = pd.to_datetime(series_df["fetched_at_utc"], utc=True)
        series_source = series_csv
    else:
        series_df, series_source = load_bin_atlas_series(pool_address, processed_dir=processed_dir)

    if series_df.empty:
        raise ValueError(f"No rows found in {series_source}")

    token_x, token_y = resolve_token_labels(pool_address, processed_dir)
    traces, liquidity_scale = prepare_snapshot_traces(series_df, zoom_bins=zoom_bins)

    if not traces:
        raise ValueError(f"No snapshots found in {series_source}")

    width = int(14 * dpi)
    height = int(8 * dpi)
    frames_per_snapshot = max(1, int(round(frame_duration_sec * fps)))
    frame_arrays: list = []

    for visible_count in range(1, len(traces) + 1):
        rgb = render_seismic_frame(
            traces,
            visible_count=visible_count,
            zoom_bins=zoom_bins,
            liquidity_scale=liquidity_scale,
            token_x=token_x,
            token_y=token_y,
            pool_address=pool_address,
            width=width,
            height=height,
        )
        frame_arrays.extend([rgb] * frames_per_snapshot)

    if output_path is None:
        PLOTS_DIR.mkdir(parents=True, exist_ok=True)
        stem = series_source.stem.replace("bin_atlas_series_", "temporal_")
        output_path = PLOTS_DIR / f"{stem}.mp4"

    output_path = output_path.resolve()
    encode_mp4(frame_arrays, output_path, fps=fps)

    duration_sec = len(frame_arrays) / fps
    print(f"Wrote {output_path}")
    print(
        f"  {len(traces)} snapshots × {frames_per_snapshot} frames "
        f"({frame_duration_sec}s each) → {len(frame_arrays)} frames, "
        f"{fps} fps, {duration_sec:.1f}s total"
    )
    return output_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render bin-atlas snapshot series (from make poll-snapshots) to MP4.",
    )
    parser.add_argument(
        "--pool",
        default=None,
        help=(
            "Pool address (default: METEORA_POOL_ADDRESS env or SOL-USDC "
            f"{DEFAULT_POOL_ADDRESS})."
        ),
    )
    parser.add_argument(
        "--series-csv",
        type=Path,
        default=None,
        help="Explicit bin_atlas_series CSV path (overrides --pool lookup).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output MP4 path (default: plots/temporal_<pool>_<ts>.mp4).",
    )
    parser.add_argument(
        "--frame-duration",
        type=float,
        default=1.0,
        help="Seconds each snapshot stays on screen (default: 1.0).",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=10,
        help="Video framerate (default: 10). Requires ffmpeg on PATH.",
    )
    parser.add_argument(
        "--zoom-bins",
        type=int,
        default=30,
        help="Neighborhood width ±N bins around active (default: 30).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Render width/height scale (default: 150 → 2100×1200).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    build_temporal_mp4(
        args.pool,
        output_path=args.output,
        series_csv=args.series_csv,
        frame_duration_sec=args.frame_duration,
        fps=args.fps,
        zoom_bins=args.zoom_bins,
        dpi=args.dpi,
    )


if __name__ == "__main__":
    main()
