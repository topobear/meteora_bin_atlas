"""Timelapse pipeline: long live poll, compress to a short MP4."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from meteora_bin_atlas.config import DEFAULT_POOL_ADDRESS, get_pool_address
from meteora_bin_atlas.temporal.datasets import (
    DATASET_IDS,
    DEFAULT_DATASET,
    DEFAULT_POLL_HZ,
    poll_interval_sec,
    resolve_rpc_dataset,
)
from meteora_bin_atlas.temporal.render import build_temporal_mp4
from meteora_bin_atlas.temporal.run import _expected_snapshot_count, _fetch_live_series, _project_root
from meteora_bin_atlas.temporal.simulate import build_simulated_series

DEFAULT_DURATION_SEC = 10.0
DEFAULT_FPS = 24
# 40 min @ 1 Hz — compressed into a 10s MP4 (240 frames @ 24 fps).
DEFAULT_SNAPSHOT_COUNT = 2400


def run_timelapse(
    pool_address: str | None = None,
    *,
    dataset: str = DEFAULT_DATASET,
    duration_sec: float = DEFAULT_DURATION_SEC,
    fps: int = DEFAULT_FPS,
    snapshot_count: int = DEFAULT_SNAPSHOT_COUNT,
    poll_hz: float = DEFAULT_POLL_HZ,
    bins_left: int = 30,
    bins_right: int = 30,
    output_path: Path | None = None,
    project_root: Path | None = None,
) -> Path:
    """Poll many snapshots at the usual rate, then render a short timelapse MP4."""
    load_dotenv()
    pool_address = pool_address or get_pool_address()
    project_root = project_root or _project_root()

    output_frames = _expected_snapshot_count(duration_sec, fps)
    if snapshot_count < output_frames:
        print(
            f"WARNING: {snapshot_count} snapshots is fewer than {output_frames} output frames; "
            f"video will be shorter than {duration_sec:.0f}s."
        )

    interval_sec = poll_interval_sec(poll_hz)
    poll_wall_sec = snapshot_count / poll_hz
    compression = snapshot_count / output_frames

    print(
        f"Timelapse run: pool={pool_address} dataset={dataset}\n"
        f"  Poll: {snapshot_count} snapshots @ {poll_hz:g} Hz "
        f"(interval {interval_sec:.2f}s, ~{poll_wall_sec / 60:.1f} min wall time)\n"
        f"  Render: subsample → {output_frames} frames → {duration_sec:.0f}s MP4 at {fps} fps "
        f"({compression:.0f}× compression)"
    )
    print("")

    series_csv: Path | None = None

    if dataset == "simulated":
        print("Source: simulated")
        _, series_csv = build_simulated_series(
            pool_address,
            snapshot_count=snapshot_count,
            interval_sec=interval_sec,
            processed_dir=project_root / "data" / "processed",
        )
    else:
        rpc_dataset = resolve_rpc_dataset(dataset, poll_hz=poll_hz)
        _fetch_live_series(
            pool_address=pool_address,
            dataset=dataset,
            snapshot_count=snapshot_count,
            rpc_backoff_sec=rpc_dataset.rpc_backoff_sec,
            interval_sec=rpc_dataset.interval_sec,
            bins_left=bins_left,
            bins_right=bins_right,
            project_root=project_root,
        )

    return build_temporal_mp4(
        pool_address,
        output_path=output_path,
        series_csv=series_csv,
        fps=fps,
        one_frame_per_snapshot=True,
        output_frames=output_frames,
        output_stem_prefix="timelapse",
        processed_dir=project_root / "data" / "processed",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Timelapse pipeline: poll many bin-atlas snapshots at the usual rate, "
            "then subsample into a short MP4 "
            f"(default: {DEFAULT_SNAPSHOT_COUNT} snaps @ 1 Hz → 10s at 24 fps)."
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
        help=(
            "Data source: alchemy (default, uses SOLANA_RPC_URL), "
            "solana-public, or simulated (no RPC)."
        ),
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
        help=(
            f"Number of snapshots to poll (default: {DEFAULT_SNAPSHOT_COUNT} "
            f"≈ {DEFAULT_SNAPSHOT_COUNT / DEFAULT_POLL_HZ / 60:.0f} min @ 1 Hz)."
        ),
    )
    parser.add_argument(
        "--poll-hz",
        type=float,
        default=DEFAULT_POLL_HZ,
        help="Live RPC poll rate in snapshots/second (default: 1).",
    )
    parser.add_argument(
        "--bins-left",
        type=int,
        default=30,
        help="Bounded fetch: bins left of active (default: 30).",
    )
    parser.add_argument(
        "--bins-right",
        type=int,
        default=30,
        help="Bounded fetch: bins right of active (default: 30).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output MP4 path (default: plots/timelapse_<pool>_<ts>.mp4).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_timelapse(
        args.pool,
        dataset=args.dataset,
        duration_sec=args.duration_sec,
        fps=args.fps,
        snapshot_count=args.count,
        poll_hz=args.poll_hz,
        bins_left=args.bins_left,
        bins_right=args.bins_right,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
