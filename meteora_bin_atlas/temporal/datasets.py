"""Dataset presets for the temporal pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DATASET_IDS = ("alchemy", "solana-public", "simulated")
DEFAULT_DATASET = "alchemy"
SOLANA_PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"

# Default temporal pacing: 2 Hz poll on Alchemy (see FETCH_LATENCY_SEC).
DEFAULT_POLL_HZ = 2.0
# Empirical bounded-fetch RPC latency on Alchemy; used to convert poll Hz → interval.
FETCH_LATENCY_SEC = 0.10


@dataclass(frozen=True)
class RpcDatasetConfig:
    dataset: str
    rpc_url: str
    rpc_backoff_sec: float
    interval_sec: float

    @property
    def rpc_host(self) -> str:
        return urlparse(self.rpc_url).hostname or "(not set)"


def poll_interval_sec(poll_hz: float) -> float:
    """Seconds to wait after each snapshot for a target poll rate."""
    if poll_hz <= 0:
        raise ValueError("poll_hz must be positive")
    return max(0.0, 1.0 / poll_hz - FETCH_LATENCY_SEC)


def resolve_rpc_dataset(dataset: str, *, poll_hz: float = DEFAULT_POLL_HZ) -> RpcDatasetConfig:
    if dataset not in DATASET_IDS:
        raise ValueError(f"--dataset must be one of: {', '.join(DATASET_IDS)}")

    if dataset == "simulated":
        raise ValueError("simulated dataset does not use Solana RPC")

    interval = poll_interval_sec(poll_hz)

    if dataset == "solana-public":
        return RpcDatasetConfig(
            dataset=dataset,
            rpc_url=SOLANA_PUBLIC_RPC_URL,
            rpc_backoff_sec=5.0,
            interval_sec=max(interval, 0.9),
        )

    rpc_url = os.getenv("SOLANA_RPC_URL", "").strip()
    if not rpc_url:
        raise ValueError(
            "alchemy dataset requires SOLANA_RPC_URL in .env (e.g. Alchemy mainnet endpoint)."
        )

    return RpcDatasetConfig(
        dataset=dataset,
        rpc_url=rpc_url,
        rpc_backoff_sec=0.0,
        interval_sec=interval,
    )
