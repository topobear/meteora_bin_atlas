"""End-to-end currency triangle temporal pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from meteora_bin_atlas.paths import PROJECT_ROOT
from meteora_bin_atlas.temporal.datasets import (
    DATASET_IDS,
    DEFAULT_DATASET,
    DEFAULT_POLL_HZ,
)
from meteora_bin_atlas.temporal.run import (
    DEFAULT_DURATION_SEC,
    DEFAULT_FPS,
    DEFAULT_SNAPSHOT_COUNT,
    _expected_snapshot_count,
    _project_root,
)
from meteora_bin_atlas.temporal.triangle.fetch import fetch_triangle_data
from meteora_bin_atlas.temporal.triangle.render import build_triangle_temporal_mp4
from meteora_bin_atlas.temporal.triangle.resolve import DEFAULT_TRIANGLE_ID, resolve_triangle


def run_triangle_temporal(
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
    """Fetch or simulate interleaved triangle legs and render composite MP4."""
    load_dotenv()
    project_root = project_root or _project_root()

    expected = _expected_snapshot_count(duration_sec, fps)
    if snapshot_count != expected:
        print(
            f"WARNING: {snapshot_count} snapshots/leg at {fps} fps → "
            f"{snapshot_count / fps:.1f}s video (expected {expected} for {duration_sec:.0f}s)."
        )

    print(
        f"Triangle temporal run: preset={triangle_id} dataset={dataset}\n"
        f"  Render: seismic strips on triangle edges · 1 snap = 1 frame → "
        f"{duration_sec:.0f}s MP4 at {fps} fps ({snapshot_count} frames/leg)"
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
        processed_dir=project_root / "data" / "processed",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Currency triangle temporal pipeline: interleaved fetch of three DLMM legs, "
            "then composite seismic strips on a triangle MP4."
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
        help="Snapshots per leg (default: 240).",
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
        help="Output MP4 path (default: plots/triangle_temporal_<id>_<ts>.mp4).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_triangle_temporal(
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
