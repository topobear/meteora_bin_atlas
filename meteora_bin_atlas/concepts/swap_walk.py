"""Swap walk and LP refill schematics."""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Patch

from meteora_bin_atlas.explore.labels import TOKEN_COLORS

SHELF_LABELS = ["bid\n(d=-1)", "active\n(d=0)", "ask\n(d=+1)"]
X_POSITIONS = [0, 1, 2]
SHELF_COLOR_LEGEND = [
    (TOKEN_COLORS["Y"], "Blue — USDC (Y) on bid shelf"),
    (TOKEN_COLORS["X"], "Purple — SOL (X) on ask shelf"),
    (TOKEN_COLORS["mix"], "Green — active shelf (price handoff)"),
    (TOKEN_COLORS["empty"], "Grey — depleted / empty shelf"),
]


def _draw_shelves(ax, title: str, active_idx: int, fills: list[str], note: str) -> None:
    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, 1.15)
    ax.set_xticks(X_POSITIONS)
    ax.set_xticklabels(SHELF_LABELS)
    ax.set_yticks([])
    ax.set_title(title, fontsize=10)

    for i, (x, fill) in enumerate(zip(X_POSITIONS, fills)):
        edge = "#111111" if i == active_idx else "#AAAAAA"
        width = 2.4 if i == active_idx else 1.8
        rect = FancyBboxPatch(
            (x - width / 2, 0.12),
            width,
            0.62,
            boxstyle="round,pad=0.03,rounding_size=0.05",
            linewidth=2.5 if i == active_idx else 1.2,
            edgecolor=edge,
            facecolor=fill,
            alpha=0.9,
        )
        ax.add_patch(rect)
        if i == active_idx:
            ax.text(x, 0.86, "ACTIVE", ha="center", fontsize=9, fontweight="bold")

    ax.text(1.0, 0.02, note, ha="center", fontsize=8.5, color="#333333")


def _add_shelf_color_legend(fig: Figure) -> None:
    handles = [Patch(facecolor=color, label=label) for color, label in SHELF_COLOR_LEGEND]
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=2,
        fontsize=8.5,
        frameon=False,
        bbox_to_anchor=(0.5, -0.06),
    )


def plot_swap_walk() -> Figure:
    """Schematic: buying SOL consumes ask-side shelves and moves active bin up."""
    fig, axes = plt.subplots(1, 4, figsize=(15, 4.2), sharey=True)

    _draw_shelves(
        axes[0],
        "1) Start",
        active_idx=1,
        fills=["#2775CA55", "#14B8A6", "#9945FF55"],
        note="Trader sends USDC, wants SOL",
    )
    _draw_shelves(
        axes[1],
        "2) Fill at P_active",
        active_idx=1,
        fills=["#2775CA55", "#14B8A699", "#9945FF55"],
        note="Active bin: Y in, X out (fixed price)",
    )
    _draw_shelves(
        axes[2],
        "3) Active shelf depleted",
        active_idx=1,
        fills=["#2775CA55", "#D1D5DB", "#9945FF55"],
        note="SOL exhausted on active shelf",
    )
    _draw_shelves(
        axes[3],
        "4) Walk book up",
        active_idx=2,
        fills=["#2775CA55", "#2775CA33", "#9945FF"],
        note="Active bin shifts to next ask shelf (higher P)",
    )

    for ax in axes[1:4]:
        ax.annotate(
            "",
            xy=(0.05, 1.05),
            xytext=(-0.95, 1.05),
            xycoords="axes fraction",
            textcoords="axes fraction",
            arrowprops=dict(arrowstyle="->", color="#555555", lw=1.5),
        )

    fig.suptitle(
        "Swap walk: buying SOL consumes ask-side shelves and moves active bin up",
        y=1.05,
        fontsize=12,
    )
    _add_shelf_color_legend(fig)
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.22)
    return fig


def build_swap_steps_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["1", "Trader input", "USDC (Y) arrives at active bin"],
            ["2", "Within-bin fill", "SOL (X) leaves active bin at fixed P_active"],
            ["3", "Shelf empty", "Active bin has no SOL left to sell"],
            ["4", "Book walk", "active_bin_id += 1; next ask shelf becomes active"],
            ["5", "Repeat", "Continue at higher price until order filled"],
        ],
        columns=["step", "event", "pool adjustment"],
    )


def plot_refill_loop() -> Figure:
    """Schematic: swaps consume shelves, then LPs deposit into bin ranges."""
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.8), sharey=True)

    _draw_shelves(
        axes[0],
        "A) After large buy",
        active_idx=2,
        fills=["#2775CA55", "#D1D5DB", "#9945FF33"],
        note="Ask shelves drained; price walked up",
    )
    _draw_shelves(
        axes[1],
        "B) LP addLiquidity",
        active_idx=2,
        fills=["#2775CA55", "#D1D5DB", "#9945FF33"],
        note="LP tx targets bin range around active",
    )
    axes[1].annotate(
        "+ X on ask bins",
        xy=(2, 0.45),
        xytext=(2, 0.95),
        ha="center",
        fontsize=8.5,
        color="#9945FF",
        arrowprops=dict(arrowstyle="->", color="#9945FF", lw=1.4),
    )
    axes[1].annotate(
        "+ Y on bid bins",
        xy=(0, 0.45),
        xytext=(0, 0.95),
        ha="center",
        fontsize=8.5,
        color="#2775CA",
        arrowprops=dict(arrowstyle="->", color="#2775CA", lw=1.4),
    )
    _draw_shelves(
        axes[2],
        "C) Shelves refilled",
        active_idx=2,
        fills=["#2775CA", "#14B8A6", "#9945FF"],
        note="Depth restored; new snapshot would show higher L",
    )

    for ax in axes[1:3]:
        ax.annotate(
            "",
            xy=(0.05, 1.08),
            xytext=(-0.95, 1.08),
            xycoords="axes fraction",
            textcoords="axes fraction",
            arrowprops=dict(arrowstyle="->", color="#555555", lw=1.5),
        )

    fig.suptitle(
        "Refill loop: swaps consume shelves → LPs deposit into bin ranges",
        y=1.06,
        fontsize=12,
    )
    _add_shelf_color_legend(fig)
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.24)
    return fig


def build_refill_steps_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                "1",
                "Post-swap state",
                "Traded bins: offering token depleted, input token accumulated (e.g. Y on ex-ask shelf)",
            ],
            [
                "2",
                "LP deposit tx",
                "Wallet submits addLiquidity over [bin_min, bin_max] with chosen shape",
            ],
            [
                "3",
                "Token placement",
                "Y lands on bid shelves, X on ask shelves, mix near active (per DLMM rules)",
            ],
            [
                "4",
                "On-chain update",
                "bin.amountX / amountY / liquiditySupply increase in those bins",
            ],
            [
                "5",
                "Next snapshot",
                "Our fetch pipeline reads the updated lattice (what this repo measures)",
            ],
        ],
        columns=["step", "event", "who / what changes"],
    )
