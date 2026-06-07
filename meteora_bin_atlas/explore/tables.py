"""Styled bin-structure tables."""

from __future__ import annotations

import pandas as pd
from pandas.io.formats.style import Styler

from meteora_bin_atlas.explore.labels import (
    TOKEN_COLORS,
    bar_color_for_bin,
    role_label,
    side_label,
    stocked_label,
)


def build_bin_structure_df(
    neighborhood_df: pd.DataFrame,
    token_x: str,
    token_y: str,
) -> pd.DataFrame:
    """Annotate neighborhood bins with side, stocked token, and role."""
    df = neighborhood_df[
        ["distance_from_active", "bin_id", "x_amount", "y_amount", "is_active_bin"]
    ].copy()
    df["side"] = df["distance_from_active"].map(side_label)
    df["stocked"] = df.apply(
        lambda row: stocked_label(row["x_amount"], row["y_amount"], token_x, token_y),
        axis=1,
    )
    df["role"] = df["distance_from_active"].map(
        lambda d: role_label(d, token_x, token_y)
    )
    return df


def _style_token_row(row: pd.Series) -> list[str]:
    color = bar_color_for_bin(
        row["x_amount"], row["y_amount"], row["distance_from_active"]
    )
    return [f"background-color: {color}22"] * len(row)


def _style_summary_row(row: pd.Series) -> list[str]:
    if row["side"] == "Active":
        color = TOKEN_COLORS["mix"]
    elif "Y only" in row["stocked"]:
        color = TOKEN_COLORS["Y"]
    elif "X only" in row["stocked"]:
        color = TOKEN_COLORS["X"]
    elif "X + Y" in row["stocked"]:
        color = TOKEN_COLORS["mix"]
    else:
        color = TOKEN_COLORS["empty"]
    return [f"background-color: {color}22"] * len(row)


def style_pattern_summary(summary: pd.DataFrame, token_x: str, token_y: str) -> Styler:
    return summary.style.apply(_style_summary_row, axis=1).set_caption(
        f"Color key: {token_y} = blue, {token_x} = purple, mix = teal"
    )


def style_bin_ladder(table_df: pd.DataFrame, token_x: str, token_y: str) -> Styler:
    display_cols = [
        "distance_from_active",
        "bin_id",
        "side",
        "stocked",
        "role",
        "x_amount",
        "y_amount",
    ]
    return (
        table_df[display_cols]
        .style.apply(_style_token_row, axis=1)
        .set_caption(
            f"Rows colored by stocked token: {token_y} (Y), {token_x} (X), or both at active"
        )
    )
