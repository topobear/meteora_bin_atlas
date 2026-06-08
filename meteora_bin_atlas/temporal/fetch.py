"""Fetch temporal snapshot series without rendering MP4."""

from __future__ import annotations

import argparse

from meteora_bin_atlas.config import DEFAULT_POOL_ADDRESS, get_pool_address
from meteora_bin_atlas.temporal.datasets import DATASET_IDS, DEFAULT_DATASET, DEFAULT_POLL_HZ
from meteora_bin_atlas.temporal.run import DEFAULT_SNAPSHOT_COUNT, fetch_temporal_data


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch or simulate a bin-atlas snapshot series "
            f"(default: {DEFAULT_SNAPSHOT_COUNT} snaps @ {DEFAULT_POLL_HZ:g} Hz, same as make temporal)."
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
        "--count",
        type=int,
        default=DEFAULT_SNAPSHOT_COUNT,
        help=f"Number of snapshots to poll (default: {DEFAULT_SNAPSHOT_COUNT}).",
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
        default=30,
        help="Bounded fetch: bins left of active (default: 30).",
    )
    parser.add_argument(
        "--bins-right",
        type=int,
        default=30,
        help="Bounded fetch: bins right of active (default: 30).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    fetch_temporal_data(
        args.pool,
        dataset=args.dataset,
        snapshot_count=args.count,
        poll_hz=args.poll_hz,
        bins_left=args.bins_left,
        bins_right=args.bins_right,
    )


if __name__ == "__main__":
    main()
