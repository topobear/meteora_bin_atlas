"""Triangle timelapse pipeline: long interleaved poll, compress to a short MP4."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from meteora_bin_atlas.temporal.datasets import (
    DATASET_IDS,
    DEFAULT_DATASET,
    DEFAULT_POLL_HZ,
    poll_interval_sec,
)
from meteora_bin_atlas.temporal.run import (
    DEFAULT_DURATION_SEC,
    DEFAULT_FPS,
    _expected_snapshot_count,
    _project_root,
)
from meteora_bin_atlas.temporal.triangle.fetch import fetch_triangle_data
from meteora_bin_atlas.temporal.triangle.render import build_triangle_temporal_mp4
from meteora_bin_atlas.temporal.triangle.resolve import DEFAULT_TRIANGLE_ID, resolve_triangle

# ~27 min @ 1.5 Hz per leg — compressed into a 10s MP4 (240 frames @ 24 fps).
DEFAULT_SNAPSHOT_COUNT = 2400


def run_triangle_timelapse(
    triangle_id: str = DEFAULT_TRIANGLE_ID,
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
    """Poll many interleaved triangle snapshots, then render a short timelapse MP4."""
    load_dotenv()
    project_root = project_root or _project_root()

    output_frames = _expected_snapshot_count(duration_sec, fps)
    if snapshot_count < output_frames:
        print(
            f"WARNING: {snapshot_count} snapshots/leg is fewer than {output_frames} output frames; "
            f"video will be shorter than {duration_sec:.0f}s."
        )

    interval_sec = poll_interval_sec(poll_hz)
    poll_wall_sec = snapshot_count / poll_hz
    compression = snapshot_count / output_frames
    total_fetches = snapshot_count * 3

    print(
        f"Triangle timelapse run: preset={triangle_id} dataset={dataset}\n"
        f"  Poll: {snapshot_count} snapshots/leg ({total_fetches} interleaved fetches) "
        f"@ {poll_hz:g} Hz (interval {interval_sec:.2f}s, ~{poll_wall_sec / 60:.1f} min wall time)\n"
        f"  Render: subsample → {output_frames} frames → {duration_sec:.0f}s MP4 at {fps} fps "
        f"({compression:.0f}× compression)"
    )
    print("")

    spec = resolve_triangle(triangle_id, project_root=project_root)
    fetch_result = fetch_triangle_data(
        spec,
        dataset=dataset,
        snapshot_count=snapshot_count,
        poll_hz=poll_hz,
        bins_left=bins_left,
        bins_right=bins_right,
        project_root=project_root,
    )

    return build_triangle_temporal_mp4(
        spec,
        fetch_result.leg_csv_paths,
        output_path=output_path,
        fps=fps,
        output_frames=output_frames,
        output_stem_prefix="triangle_timelapse",
        processed_dir=project_root / "data" / "processed",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Triangle timelapse pipeline: interleaved fetch of three DLMM legs at the usual rate, "
            "then subsample into a short composite MP4 "
            f"(default: {DEFAULT_SNAPSHOT_COUNT} snaps/leg @ {DEFAULT_POLL_HZ:g} Hz → 10s at 24 fps)."
        ),
    )
    parser.add_argument(
        "--triangle",
        default=DEFAULT_TRIANGLE_ID,
        help=f"Triangle preset id (default: {DEFAULT_TRIANGLE_ID}).",
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
        help=(
            f"Snapshots per leg to poll (default: {DEFAULT_SNAPSHOT_COUNT} "
            f"≈ {DEFAULT_SNAPSHOT_COUNT / DEFAULT_POLL_HZ / 60:.0f} min @ {DEFAULT_POLL_HZ:g} Hz)."
        ),
    )
    parser.add_argument(
        "--poll-hz",
        type=float,
        default=DEFAULT_POLL_HZ,
        help=f"Live RPC poll rate (default: {DEFAULT_POLL_HZ:g}).",
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
        help="Output MP4 path (default: plots/triangle_timelapse_<id>_<ts>.mp4).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_triangle_timelapse(
        args.triangle,
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
