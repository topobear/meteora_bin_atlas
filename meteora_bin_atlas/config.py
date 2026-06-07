"""Environment and RPC configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

# Default SOL-USDC pool from data/manual_pools.json (same as Makefile POOL=).
DEFAULT_POOL_ADDRESS = "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6"


@dataclass(frozen=True)
class RpcInfo:
    host: str
    cluster: str


def get_pool_address() -> str:
    """Return pool address from METEORA_POOL_ADDRESS or the default SOL-USDC pool."""
    return os.getenv("METEORA_POOL_ADDRESS", DEFAULT_POOL_ADDRESS)


def get_rpc_info() -> RpcInfo:
    """Return RPC host and cluster without exposing the full URL (may contain API keys)."""
    rpc_url = os.getenv("SOLANA_RPC_URL", "")
    host = urlparse(rpc_url).hostname or "(not set)"
    cluster = os.getenv("SOLANA_CLUSTER", "mainnet-beta")
    return RpcInfo(host=host, cluster=cluster)
