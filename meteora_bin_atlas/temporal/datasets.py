"""Dataset presets for the temporal pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DATASET_IDS = ("alchemy", "solana-public", "simulated")
DEFAULT_DATASET = "alchemy"
SOLANA_PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"


@dataclass(frozen=True)
class RpcDatasetConfig:
    dataset: str
    rpc_url: str
    rpc_backoff_sec: int
    interval_sec: int

    @property
    def rpc_host(self) -> str:
        return urlparse(self.rpc_url).hostname or "(not set)"


def resolve_rpc_dataset(dataset: str) -> RpcDatasetConfig:
    if dataset not in DATASET_IDS:
        raise ValueError(f"--dataset must be one of: {', '.join(DATASET_IDS)}")

    if dataset == "simulated":
        raise ValueError("simulated dataset does not use Solana RPC")

    if dataset == "solana-public":
        return RpcDatasetConfig(
            dataset=dataset,
            rpc_url=SOLANA_PUBLIC_RPC_URL,
            rpc_backoff_sec=15,
            interval_sec=10,
        )

    rpc_url = os.getenv("SOLANA_RPC_URL", "").strip()
    if not rpc_url:
        raise ValueError(
            "alchemy dataset requires SOLANA_RPC_URL in .env (e.g. Alchemy mainnet endpoint)."
        )

    return RpcDatasetConfig(
        dataset=dataset,
        rpc_url=rpc_url,
        rpc_backoff_sec=2,
        interval_sec=5,
    )
