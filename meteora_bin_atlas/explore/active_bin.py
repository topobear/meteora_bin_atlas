"""Active-bin snapshot summary."""

from __future__ import annotations

import pandas as pd


def build_active_summary(
    pool_address: str,
    pool_label: str,
    pool_snapshot: dict,
    active_bin: pd.Series,
    token_x: str,
    token_y: str,
    bin_step: int | float | None,
) -> pd.DataFrame:
    """Build a field / value / explanation table for the active bin."""
    return pd.DataFrame(
        [
            ("pool_address", pool_address, "Solana pubkey of this DLMM pool account."),
            ("pool_label", pool_label, "Human-readable pair label from Meteora discovery."),
            (
                "token_x_mint",
                pool_snapshot["token_x_mint"],
                f"Mint pubkey for token X ({token_x}); ask-side shelves above active hold X.",
            ),
            (
                "token_y_mint",
                pool_snapshot["token_y_mint"],
                f"Mint pubkey for token Y ({token_y}); bid-side shelves below active hold Y.",
            ),
            (
                "bin_step_bps",
                bin_step,
                "Basis-point spacing between adjacent bin prices on the lattice.",
            ),
            (
                "active_bin_id",
                int(active_bin["bin_id"]),
                "Global bin coordinate where price sits now (the pool's local origin).",
            ),
            (
                "bin_array_index",
                int(active_bin["bin_array_index"]),
                "Which on-chain bin-array account stores this shelf.",
            ),
            (
                "distance_from_active",
                int(active_bin["distance_from_active"]),
                "Offset from the active shelf; always 0 on this row.",
            ),
            (
                f"price ({token_y} per {token_x})",
                pool_snapshot["active_bin_price"],
                f"Human-readable fixed trade price at this shelf ({token_y} per 1 {token_x}).",
            ),
            (
                "price_on_chain_Q64",
                active_bin["price"],
                "Raw on-chain fixed price encoding (Q64) for this bin.",
            ),
            (
                f"x_amount ({token_x}, raw)",
                int(active_bin["x_amount"]),
                f"{token_x} reserve on the active shelf (smallest on-chain units).",
            ),
            (
                f"y_amount ({token_y}, raw)",
                int(active_bin["y_amount"]),
                f"{token_y} reserve on the active shelf (smallest on-chain units).",
            ),
            (
                "liquidity (raw on-chain units)",
                str(active_bin["liquidity"]),
                "Liquidity scalar L at this bin (on-chain units; at fixed P, L relates to x and y).",
            ),
            (
                "fetched_at_utc",
                active_bin["fetched_at_utc"],
                "UTC timestamp when this bin atlas snapshot was written.",
            ),
            (
                "raw_bin_array_pubkey",
                active_bin["raw_bin_array_pubkey"],
                "Solana pubkey of the bin-array account containing this shelf.",
            ),
        ],
        columns=["field", "value", "explanation"],
    ).set_index("field")
