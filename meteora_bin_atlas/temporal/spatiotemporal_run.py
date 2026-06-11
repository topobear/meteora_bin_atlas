"""End-to-end spatiotemporal pipeline: fetch or simulate series, render 3D MP4."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from meteora_bin_atlas.config import DEFAULT_POOL_ADDRESS, get_pool_address
from meteora_bin_atlas.paths import DATA_PROCESSED, PLOTS_DIR
from meteora_bin_atlas.temporal.datasets import (
    DATASET_IDS,
    DEFAULT_DATASET,
    DEFAULT_POLL_HZ,
)
from meteora_bin_atlas.temporal.load import load_bin_atlas_series, load_simulated_bin_atlas_series
from meteora_bin_atlas.temporal.render import resolve_token_labels
from meteora_bin_atlas.temporal.run import (
    DEFAULT_DURATION_SEC,
    DEFAULT_FPS,
    DEFAULT_SNAPSHOT_COUNT,
    _expected_snapshot_count,
    fetch_temporal_data,
)
from meteora_bin_atlas.temporal.seismic import prepare_snapshot_traces
from meteora_bin_atlas.temporal.spatiotemporal import (
    SPATIOTEMPORAL_DEFAULT_BINS_LEFT,
    SPATIOTEMPORAL_DEFAULT_BINS_RIGHT,
    SPATIOTEMPORAL_HEIGHT_IN,
    SPATIOTEMPORAL_WIDTH_IN,
    build_spatiotemporal_mp4,
)


def run_spatiotemporal(
    pool_address: str | None = None,
    *,
    dataset: str = DEFAULT_DATASET,
    duration_sec: float = DEFAULT_DURATION_SEC,
    fps: int = DEFAULT_FPS,
    snapshot_count: int = DEFAULT_SNAPSHOT_COUNT,
    poll_hz: float = DEFAULT_POLL_HZ,
    bins_left: int = SPATIOTEMPORAL_DEFAULT_BINS_LEFT,
    bins_right: int = SPATIOTEMPORAL_DEFAULT_BINS_RIGHT,
    output_path: Path | None = None,
    project_root: Path | None = None,
    dpi: int = 100,
) -> Path:
    """Fetch or simulate a snapshot series and render a 3D spatiotemporal MP4."""
    load_dotenv()
    pool_address = pool_address or get_pool_address()
    project_root = project_root or Path(__file__).resolve().parents[2]

    expected = _expected_snapshot_count(duration_sec, fps)
    if snapshot_count != expected:
        print(
            f"WARNING: {snapshot_count} snapshots at {fps} fps → "
            f"{snapshot_count / fps:.1f}s video (expected {expected} for {duration_sec:.0f}s)."
        )

    print(
        f"Spatiotemporal run: pool={pool_address} dataset={dataset}\n"
        f"  Render: 3D platformer view · 1 snap = 1 frame → {duration_sec:.0f}s MP4 at {fps} fps "
        f"({snapshot_count} frames)"
    )
    print("")

    series_csv = fetch_temporal_data(
        pool_address,
        dataset=dataset,
        snapshot_count=snapshot_count,
        poll_hz=poll_hz,
        bins_left=bins_left,
        bins_right=bins_right,
        project_root=project_root,
    )

    if series_csv is not None:
        import pandas as pd

        series_df = pd.read_csv(series_csv)
        if "fetched_at_utc" in series_df.columns:
            series_df["fetched_at_utc"] = pd.to_datetime(series_df["fetched_at_utc"], utc=True)
        series_source = series_csv
    elif dataset == "simulated":
        series_df, series_source = load_simulated_bin_atlas_series(
            pool_address,
            simulated_dir=project_root / "data" / "simulated",
        )
    else:
        series_df, series_source = load_bin_atlas_series(
            pool_address,
            processed_dir=project_root / "data" / "processed",
        )

    if series_df.empty:
        raise ValueError(f"No rows found in {series_source}")

    token_x, token_y = resolve_token_labels(pool_address, DATA_PROCESSED)
    traces, liquidity_scale, atlas_frame = prepare_snapshot_traces(series_df)

    if not traces:
        raise ValueError(f"No snapshots found in {series_source}")

    width = int(SPATIOTEMPORAL_WIDTH_IN * dpi)
    height = int(SPATIOTEMPORAL_HEIGHT_IN * dpi)

    if output_path is None:
        PLOTS_DIR.mkdir(parents=True, exist_ok=True)
        stem = series_source.stem.replace("bin_atlas_series_", "spatiotemporal_")
        output_path = PLOTS_DIR / f"{stem}.mp4"

    output_path = output_path.resolve()
    build_spatiotemporal_mp4(
        traces,
        atlas_frame=atlas_frame,
        liquidity_scale=liquidity_scale,
        token_x=token_x,
        token_y=token_y,
        pool_address=pool_address,
        output_path=output_path,
        fps=fps,
        width=width,
        height=height,
        dpi=dpi,
    )

    duration = len(traces) / fps
    print(f"Wrote {output_path}")
    print(
        f"  {len(traces)} snapshots × 1 frame (3D platformer) "
        f"→ {len(traces)} frames, {fps} fps, {duration:.1f}s total"
    )
    return output_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Spatiotemporal 3D pipeline: fetch or simulate bin-atlas series, "
            "then render a platformer-view MP4 (bin × time × liquidity)."
        ),
    )
    parser.add_argument(
        "--pool",
        default=None,
        help=f"Pool address (default: METEORA_POOL_ADDRESS or {DEFAULT_POOL_ADDRESS}).",
    )
    parser.add_argument(
        "--dataset",
        choices=DATASET_IDS,
        default=DEFAULT_DATASET,
        help="Data source: alchemy, solana-public, or simulated.",
    )
    parser.add_argument(
        "--duration-sec",
        type=float,
        default=DEFAULT_DURATION_SEC,
        help="Target MP4 duration in seconds (default: 10).",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        help="MP4 framerate (default: 24).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_SNAPSHOT_COUNT,
        help="Number of snapshots to poll (default: 240).",
    )
    parser.add_argument(
        "--poll-hz",
        type=float,
        default=DEFAULT_POLL_HZ,
        help=f"Live RPC poll rate in snapshots/second (default: {DEFAULT_POLL_HZ:g}).",
    )
    parser.add_argument(
        "--bins-left",
        type=int,
        default=SPATIOTEMPORAL_DEFAULT_BINS_LEFT,
        help=f"Bounded fetch: bins left of active (default: {SPATIOTEMPORAL_DEFAULT_BINS_LEFT}).",
    )
    parser.add_argument(
        "--bins-right",
        type=int,
        default=SPATIOTEMPORAL_DEFAULT_BINS_RIGHT,
        help=f"Bounded fetch: bins right of active (default: {SPATIOTEMPORAL_DEFAULT_BINS_RIGHT}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output MP4 path (default: plots/spatiotemporal_<pool>_<ts>.mp4).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=100,
        help=f"Render scale (default: 100 → {int(SPATIOTEMPORAL_WIDTH_IN * 100)}×{int(SPATIOTEMPORAL_HEIGHT_IN * 100)}).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_spatiotemporal(
        args.pool,
        dataset=args.dataset,
        duration_sec=args.duration_sec,
        fps=args.fps,
        snapshot_count=args.count,
        poll_hz=args.poll_hz,
        bins_left=args.bins_left,
        bins_right=args.bins_right,
        output_path=args.output,
        dpi=args.dpi,
    )


if __name__ == "__main__":
    main()
