"""Render temporal bin-atlas snapshots to MP4."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.animation import FFMpegWriter
from matplotlib.backends.backend_agg import FigureCanvasAgg

from meteora_bin_atlas.config import DEFAULT_POOL_ADDRESS, get_pool_address
from meteora_bin_atlas.explore.labels import parse_token_labels
from meteora_bin_atlas.explore.plots import plot_liquidity_by_bin
from meteora_bin_atlas.paths import DATA_PROCESSED, PLOTS_DIR
from meteora_bin_atlas.temporal.load import load_bin_atlas_series


def _neighborhood_for_snapshot(group: pd.DataFrame, zoom_bins: int) -> pd.DataFrame:
    neighborhood = (
        group[group["distance_from_active"].between(-zoom_bins, zoom_bins)]
        .sort_values("bin_id")
        .copy()
    )
    neighborhood["liquidity"] = pd.to_numeric(neighborhood["liquidity"], errors="coerce").fillna(0)
    neighborhood["x_amount"] = pd.to_numeric(neighborhood["x_amount"], errors="coerce").fillna(0)
    neighborhood["y_amount"] = pd.to_numeric(neighborhood["y_amount"], errors="coerce").fillna(0)
    return neighborhood


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


def _render_frame_to_rgb(
    neighborhood_df: pd.DataFrame,
    *,
    token_x: str,
    token_y: str,
    zoom_bins: int,
    y_max: float,
    subtitle: str,
    dpi: int,
) -> np.ndarray:
    fig = plot_liquidity_by_bin(neighborhood_df, token_x, token_y, zoom_bins=zoom_bins)
    ax = fig.axes[0]
    ax.set_ylim(0, y_max)
    ax.set_title(
        f"Meteora DLMM liquidity by bin (±{zoom_bins} around active)\n{subtitle}",
        fontsize=12,
    )

    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    rgba = np.asarray(canvas.buffer_rgba())
    plt.close(fig)
    return rgba[:, :, :3]


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
    """Build an MP4 from the latest bin-atlas series CSV for a pool."""
    pool_address = pool_address or get_pool_address()

    if series_csv is not None:
        series_df = pd.read_csv(series_csv)
        if "fetched_at_utc" in series_df.columns:
            series_df["fetched_at_utc"] = pd.to_datetime(series_df["fetched_at_utc"], utc=True)
        series_source = series_csv
    else:
        series_df, series_source = load_bin_atlas_series(pool_address, processed_dir=processed_dir)

    snapshot_indices = sorted(series_df["snapshot_index"].unique())
    if len(snapshot_indices) < 1:
        raise ValueError(f"No snapshots found in {series_source}")

    token_x, token_y = resolve_token_labels(pool_address, processed_dir)

    liquidity = pd.to_numeric(series_df["liquidity"], errors="coerce").fillna(0)
    y_max = float(liquidity.max()) * 1.05 if not liquidity.empty else 1.0
    if y_max <= 0:
        y_max = 1.0

    frames_per_snapshot = max(1, int(round(frame_duration_sec * fps)))
    frame_arrays: list[np.ndarray] = []

    for snapshot_index in snapshot_indices:
        group = series_df[series_df["snapshot_index"] == snapshot_index]
        neighborhood = _neighborhood_for_snapshot(group, zoom_bins)
        fetched_at = group["fetched_at_utc"].iloc[0]
        if hasattr(fetched_at, "strftime"):
            fetched_label = fetched_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            fetched_label = str(fetched_at)

        subtitle = f"snapshot {snapshot_index} · {fetched_label}"
        rgb = _render_frame_to_rgb(
            neighborhood,
            token_x=token_x,
            token_y=token_y,
            zoom_bins=zoom_bins,
            y_max=y_max,
            subtitle=subtitle,
            dpi=dpi,
        )
        frame_arrays.extend([rgb] * frames_per_snapshot)

    if output_path is None:
        PLOTS_DIR.mkdir(parents=True, exist_ok=True)
        stem = series_source.stem.replace("bin_atlas_series_", "temporal_")
        output_path = PLOTS_DIR / f"{stem}.mp4"

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(14, 6), dpi=dpi)
    ax.axis("off")
    image = ax.imshow(frame_arrays[0])
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    writer = FFMpegWriter(fps=fps)
    with writer.saving(fig, str(output_path), dpi=dpi):
        for frame in frame_arrays:
            image.set_data(frame)
            writer.grab_frame()

    plt.close(fig)

    duration_sec = len(frame_arrays) / fps
    print(f"Wrote {output_path}")
    print(
        f"  {len(snapshot_indices)} snapshots × {frames_per_snapshot} frames "
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
        help="Render DPI for each frame (default: 150).",
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
