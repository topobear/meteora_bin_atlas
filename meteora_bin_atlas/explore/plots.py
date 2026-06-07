"""Liquidity distribution plots."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from meteora_bin_atlas.explore.labels import TOKEN_COLORS, bar_color_for_bin


def plot_liquidity_by_bin(
    neighborhood_df: pd.DataFrame,
    token_x: str,
    token_y: str,
    zoom_bins: int = 30,
) -> Figure:
    """Bar chart of liquidity by distance from active bin, colored by stocked token."""
    bar_colors = neighborhood_df.apply(
        lambda row: bar_color_for_bin(
            row["x_amount"], row["y_amount"], row["distance_from_active"]
        ),
        axis=1,
    )

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.bar(
        neighborhood_df["distance_from_active"],
        neighborhood_df["liquidity"],
        width=0.8,
        color=bar_colors,
    )
    ax.axvline(0, linestyle="--", linewidth=1)
    ax.set_xlim(-zoom_bins - 1, zoom_bins + 1)
    ax.set_xlabel("Distance from active bin")
    ax.set_ylabel("Liquidity (raw on-chain units)")
    ax.set_title(f"Meteora DLMM liquidity by bin (±{zoom_bins} around active)")
    ax.legend(
        handles=[
            Patch(facecolor=TOKEN_COLORS["Y"], label=f"{token_y} (Y)"),
            Patch(facecolor=TOKEN_COLORS["X"], label=f"{token_x} (X)"),
            Patch(
                facecolor=TOKEN_COLORS["mix"],
                label=f"{token_x} + {token_y} (active mix)",
            ),
        ],
        loc="upper right",
    )
    fig.tight_layout()
    ax.grid(True)
    return fig
