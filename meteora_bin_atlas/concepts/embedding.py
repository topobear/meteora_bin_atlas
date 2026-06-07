"""Coordinate embedding: (P, L) shelves pullback to (x, y) reserves."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

SHELF_COLORS = {"bid": "#2775CA", "active": "#14B8A6", "ask": "#9945FF"}
SHELF_KEYS = ["bid", "active", "ask"]
SHELF_LABELS = ["Bid shelf", "Active shelf", "Ask shelf"]


def build_toy_embedding_df(
    p_active: float = 150.0,
    bin_step_bps: int = 25,
    token_x: str = "SOL",
    token_y: str = "USDC",
) -> pd.DataFrame:
    """Build illustrative embedding rows around a notional active price."""
    step = bin_step_bps / 10_000
    y_col = f"y ({token_y})"

    rows = [
        {
            "distance_from_active": -1,
            "side": "Bid shelf",
            "P (USDC/SOL)": round(p_active / (1 + step), 2),
            "x (SOL)": 0.0,
            y_col: 1_800_000.0,
        },
        {
            "distance_from_active": 0,
            "side": "Active shelf",
            "P (USDC/SOL)": p_active,
            "x (SOL)": 1.68,
            y_col: 1_837_912.0,
        },
        {
            "distance_from_active": 1,
            "side": "Ask shelf",
            "P (USDC/SOL)": round(p_active * (1 + step), 2),
            "x (SOL)": 28_824.88,
            y_col: 0.0,
        },
    ]

    df = pd.DataFrame(rows)
    df["L = P·x + y (USDC)"] = df["P (USDC/SOL)"] * df["x (SOL)"] + df[y_col]
    df["pullback (x,y)"] = [
        "x=0  →  y=L",
        "mix on line y = L − P·x",
        "y=0  →  x=L/P",
    ]
    return df


def plot_coordinate_embedding(
    embedding_df: pd.DataFrame,
    token_x: str = "SOL",
    token_y: str = "USDC",
) -> Figure:
    """Plot pullback to reserve space and the forward (distance, L) ladder."""
    y_col = f"y ({token_y})"
    rows = embedding_df.to_dict("records")

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

    ax = axes[0]
    x_line = np.linspace(0, 32_000, 200)
    for row, label, key in zip(rows, SHELF_LABELS, SHELF_KEYS):
        p = row["P (USDC/SOL)"]
        x = row["x (SOL)"]
        y = row[y_col]
        l_val = p * x + y
        ax.plot(x_line, l_val - p * x_line, color=SHELF_COLORS[key], alpha=0.35, linewidth=1.5)
        ax.scatter([x], [y], s=120, color=SHELF_COLORS[key], edgecolor="white", linewidth=1.2, zorder=3)
        ax.annotate(
            f"{label}\nP={p:.2f}",
            (x, y),
            textcoords="offset points",
            xytext=(8, 8),
            fontsize=9,
        )

    ax.set_xlim(-500, 32_000)
    ax.set_ylim(-200_000, 2_200_000)
    ax.set_xlabel(f"x ({token_x})")
    ax.set_ylabel(f"y ({token_y})")
    ax.set_title("Pullback to reserve space: bins sit on iso-L lines")
    ax.grid(True, alpha=0.25)

    ax2 = axes[1]
    distances = embedding_df["distance_from_active"]
    l_values = embedding_df["L = P·x + y (USDC)"]
    bar_colors = [SHELF_COLORS[k] for k in SHELF_KEYS]
    ax2.bar(distances, l_values / 1e6, width=0.65, color=bar_colors)
    ax2.axvline(0, linestyle="--", color="#555555", linewidth=1)
    ax2.set_xticks(distances)
    ax2.set_xlabel("Distance from active bin")
    ax2.set_ylabel("Liquidity L (millions of USDC)")
    ax2.set_title("Forward map: shelf index → (P, L)")
    for d, l_val in zip(distances, l_values):
        p_val = embedding_df.loc[
            embedding_df["distance_from_active"] == d, "P (USDC/SOL)"
        ].iloc[0]
        ax2.text(d, l_val / 1e6 + 0.05, f"P={p_val:.2f}", ha="center", fontsize=9)

    fig.suptitle(
        "Coordinate embedding: (P, L) shelves pullback to (x, y) reserves",
        y=1.02,
        fontsize=12,
    )
    fig.tight_layout()
    return fig
