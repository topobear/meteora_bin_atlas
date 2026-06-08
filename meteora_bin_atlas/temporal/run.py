"""End-to-end temporal pipeline: fetch or simulate series, then render MP4."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from meteora_bin_atlas.config import DEFAULT_POOL_ADDRESS, get_pool_address
from meteora_bin_atlas.temporal.datasets import DATASET_IDS, DEFAULT_DATASET, resolve_rpc_dataset
from meteora_bin_atlas.temporal.render import build_temporal_mp4
from meteora_bin_atlas.temporal.simulate import build_simulated_series

DEFAULT_DURATION_SEC = 10.0
DEFAULT_FPS = 24
DEFAULT_SNAPSHOT_COUNT = 10


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _frame_duration(duration_sec: float, snapshot_count: int) -> float:
    if snapshot_count <= 0:
        raise ValueError("snapshot count must be positive")
    return duration_sec / snapshot_count


def _fetch_live_series(
    *,
    pool_address: str,
    dataset: str,
    snapshot_count: int,
    rpc_backoff_sec: int,
    interval_sec: int,
    bins_left: int,
    bins_right: int,
    project_root: Path,
) -> None:
    rpc_dataset = resolve_rpc_dataset(dataset)
    env = os.environ.copy()
    env["SOLANA_RPC_URL"] = rpc_dataset.rpc_url

    cmd = [
        "npm",
        "run",
        "temporal",
        "--",
        "--pool",
        pool_address,
        "--dataset",
        dataset,
        "--count",
        str(snapshot_count),
        "--rpc-backoff-sec",
        str(rpc_backoff_sec),
        "--interval-sec",
        str(interval_sec),
        "--bins-left",
        str(bins_left),
        "--bins-right",
        str(bins_right),
    ]

    print(f"Fetching live series via {dataset} ({rpc_dataset.rpc_host})...")
    result = subprocess.run(cmd, cwd=project_root, env=env, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def run_temporal(
    pool_address: str | None = None,
    *,
    dataset: str = DEFAULT_DATASET,
    duration_sec: float = DEFAULT_DURATION_SEC,
    fps: int = DEFAULT_FPS,
    snapshot_count: int = DEFAULT_SNAPSHOT_COUNT,
    bins_left: int = 30,
    bins_right: int = 30,
    output_path: Path | None = None,
    project_root: Path | None = None,
) -> Path:
    """Fetch or simulate a snapshot series and render a temporal MP4."""
    load_dotenv()
    pool_address = pool_address or get_pool_address()
    project_root = project_root or _project_root()
    frame_duration = _frame_duration(duration_sec, snapshot_count)

    print(
        f"Temporal run: pool={pool_address} dataset={dataset} "
        f"{snapshot_count} snapshots → {duration_sec:.0f}s MP4 at {fps} fps "
        f"({frame_duration:.2f}s per snapshot)"
    )
    print("")

    series_csv: Path | None = None

    if dataset == "simulated":
        _, series_csv = build_simulated_series(
            pool_address,
            snapshot_count=snapshot_count,
            interval_sec=frame_duration,
            processed_dir=project_root / "data" / "processed",
        )
    else:
        rpc_dataset = resolve_rpc_dataset(dataset)
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
        frame_duration_sec=frame_duration,
        fps=fps,
        processed_dir=project_root / "data" / "processed",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "End-to-end temporal pipeline: fetch or simulate bin-atlas series, "
            "then render MP4 (default 10s at 24 fps)."
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
        help="Number of snapshots in the series (default: 10).",
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
        help="Output MP4 path (default: plots/temporal_<pool>_<ts>.mp4).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_temporal(
        args.pool,
        dataset=args.dataset,
        duration_sec=args.duration_sec,
        fps=args.fps,
        snapshot_count=args.count,
        bins_left=args.bins_left,
        bins_right=args.bins_right,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
