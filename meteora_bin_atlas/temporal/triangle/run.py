"""End-to-end currency triangle temporal pipeline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from meteora_bin_atlas.paths import PROJECT_ROOT
from meteora_bin_atlas.temporal.datasets import (
    DATASET_IDS,
    DEFAULT_DATASET,
    DEFAULT_POLL_HZ,
    poll_interval_sec,
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

# Gentler default for long realtime captures (per-leg snap every ~4s).
DEFAULT_RT_POLL_HZ = 0.75
DEFAULT_RT_POLL_MINUTES = 5.0


@dataclass(frozen=True)
class TriangleRealtimeParams:
    snapshot_count: int
    fps: float
    duration_sec: float
    poll_hz: float
    poll_minutes: float
    leg_hz: float

    @property
    def poll_wall_sec(self) -> float:
        return self.snapshot_count * 3.0 / self.poll_hz


def resolve_triangle_realtime_params(
    poll_minutes: float,
    *,
    poll_hz: float = DEFAULT_RT_POLL_HZ,
) -> TriangleRealtimeParams:
    """Match poll wall time to MP4 duration (1 snap/leg = 1 frame, fps = poll_hz/3)."""
    if poll_minutes <= 0:
        raise ValueError("poll_minutes must be positive")
    if poll_hz <= 0:
        raise ValueError("poll_hz must be positive")

    poll_sec = poll_minutes * 60.0
    leg_hz = poll_hz / 3.0
    snapshot_count = max(1, int(round(poll_sec * leg_hz)))
    fps = poll_hz / 3.0
    duration_sec = snapshot_count / fps

    return TriangleRealtimeParams(
        snapshot_count=snapshot_count,
        fps=fps,
        duration_sec=duration_sec,
        poll_hz=poll_hz,
        poll_minutes=poll_minutes,
        leg_hz=leg_hz,
    )


def run_triangle_temporal(
    triangle_id: str = DEFAULT_TRIANGLE_ID,
    *,
    dataset: str = DEFAULT_DATASET,
    duration_sec: float = DEFAULT_DURATION_SEC,
    fps: float = DEFAULT_FPS,
    snapshot_count: int = DEFAULT_SNAPSHOT_COUNT,
    poll_hz: float = DEFAULT_POLL_HZ,
    bins_left: int = 30,
    bins_right: int = 30,
    output_path: Path | None = None,
    project_root: Path | None = None,
    realtime: bool = False,
    poll_minutes: float = DEFAULT_RT_POLL_MINUTES,
) -> Path:
    """Fetch or simulate interleaved triangle legs and render composite MP4."""
    load_dotenv()
    project_root = project_root or _project_root()

    rt_params: TriangleRealtimeParams | None = None
    if realtime:
        rt_params = resolve_triangle_realtime_params(poll_minutes, poll_hz=poll_hz)
        snapshot_count = rt_params.snapshot_count
        fps = rt_params.fps
        duration_sec = rt_params.duration_sec
        poll_hz = rt_params.poll_hz

    expected = _expected_snapshot_count(duration_sec, fps)
    if not realtime and snapshot_count != expected:
        print(
            f"WARNING: {snapshot_count} snapshots/leg at {fps} fps → "
            f"{snapshot_count / fps:.1f}s video (expected {expected} for {duration_sec:.0f}s)."
        )

    if realtime and rt_params is not None:
        interval_sec = poll_interval_sec(poll_hz)
        print(
            f"Triangle temporal run (realtime): preset={triangle_id} dataset={dataset}\n"
            f"  Poll: ~{rt_params.poll_minutes:g} min @ {poll_hz:g} Hz interleaved "
            f"(~{rt_params.leg_hz:.3g} snaps/s/leg, interval {interval_sec:.2f}s, "
            f"~{rt_params.poll_wall_sec / 60:.1f} min wall)\n"
            f"  Render: 1 snap = 1 frame → ~{duration_sec / 60:.1f} min MP4 @ {fps:.4g} fps "
            f"({snapshot_count} frames/leg)\n"
            f"  Timelapse later: ffmpeg -filter:v \"setpts=0.1*PTS\" for 10× speedup"
        )
    else:
        print(
            f"Triangle temporal run: preset={triangle_id} dataset={dataset}\n"
            f"  Render: seismic strips on triangle edges · 1 snap = 1 frame → "
            f"{duration_sec:.0f}s MP4 at {fps:g} fps ({snapshot_count} frames/leg)"
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
        "--realtime",
        action="store_true",
        help=(
            "1:1 poll → footage: set count/fps/duration from --poll-minutes and --poll-hz "
            f"(default {DEFAULT_RT_POLL_MINUTES:g} min @ {DEFAULT_RT_POLL_HZ:g} Hz)."
        ),
    )
    parser.add_argument(
        "--poll-minutes",
        type=float,
        default=DEFAULT_RT_POLL_MINUTES,
        help=f"Realtime mode: target poll and MP4 duration in minutes (default: {DEFAULT_RT_POLL_MINUTES:g}).",
    )
    parser.add_argument(
        "--duration-sec",
        type=float,
        default=DEFAULT_DURATION_SEC,
        help="Target MP4 duration in seconds (default: 10). Ignored with --realtime.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help="MP4 framerate (default: 24). Ignored with --realtime.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_SNAPSHOT_COUNT,
        help="Snapshots per leg (default: 240). Ignored with --realtime.",
    )
    parser.add_argument(
        "--poll-hz",
        type=float,
        default=DEFAULT_POLL_HZ,
        help=(
            f"Live RPC poll rate (default: {DEFAULT_POLL_HZ:g}; "
            f"realtime default {DEFAULT_RT_POLL_HZ:g} when --realtime)."
        ),
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
    poll_hz = DEFAULT_RT_POLL_HZ if args.realtime and args.poll_hz == DEFAULT_POLL_HZ else args.poll_hz
    run_triangle_temporal(
        args.triangle,
        dataset=args.dataset,
        duration_sec=args.duration_sec,
        fps=args.fps,
        snapshot_count=args.count,
        poll_hz=poll_hz,
        bins_left=args.bins_left,
        bins_right=args.bins_right,
        output_path=args.output,
        realtime=args.realtime,
        poll_minutes=args.poll_minutes,
    )


if __name__ == "__main__":
    main()
