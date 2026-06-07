"""Neighborhood windowing and sanity checks."""

from __future__ import annotations

import pandas as pd


def print_sanity_checks(df: pd.DataFrame) -> None:
    """Print basic range checks for a bin-atlas dataframe."""
    print("bin_id range:", df["bin_id"].min(), df["bin_id"].max())
    print(
        "distance_from_active range:",
        df["distance_from_active"].min(),
        df["distance_from_active"].max(),
    )
    print(
        "active bin rows:",
        df["is_active_bin"].astype(str).str.lower().eq("true").sum(),
    )


def prepare_neighborhood(df: pd.DataFrame, zoom_bins: int = 30) -> pd.DataFrame:
    """Return a numeric neighborhood window around the active bin."""
    neighborhood = (
        df[df["distance_from_active"].between(-zoom_bins, zoom_bins)]
        .sort_values("bin_id")
        .copy()
    )
    neighborhood["liquidity"] = pd.to_numeric(neighborhood["liquidity"], errors="coerce").fillna(0)
    neighborhood["x_amount"] = pd.to_numeric(neighborhood["x_amount"], errors="coerce").fillna(0)
    neighborhood["y_amount"] = pd.to_numeric(neighborhood["y_amount"], errors="coerce").fillna(0)
    print(f"\nNeighborhood rows (±{zoom_bins} bins):", len(neighborhood))
    return neighborhood
