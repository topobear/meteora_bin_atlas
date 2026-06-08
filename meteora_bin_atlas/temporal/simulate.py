"""Simulate a long bin-atlas snapshot series from a real seed snapshot."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from meteora_bin_atlas.config import DEFAULT_POOL_ADDRESS, get_pool_address
from meteora_bin_atlas.paths import DATA_PROCESSED
from meteora_bin_atlas.temporal.load import load_bin_atlas_series

# Empirical active-bin steps from the SOL-USDC poll (snapshot deltas).
DEFAULT_ACTIVE_DELTAS = (-2, -1, 0, 0, 0, 1, 1, 2, -1, 2, -2, 1, -1, 3, -1, 4)
BIN_STEP_BPS = 4


def _active_bin_id(group: pd.DataFrame) -> int:
    mask = group["is_active_bin"].astype(str).str.lower().eq("true")
    if not mask.any():
        mask = group["is_active_bin"] == True  # noqa: E712
    return int(group.loc[mask, "bin_id"].iloc[0])


def _seed_snapshot(series_df: pd.DataFrame, seed_index: int | None) -> pd.DataFrame:
    indices = sorted(series_df["snapshot_index"].unique())
    if not indices:
        raise ValueError("Seed series has no snapshots")
    chosen = indices[-1] if seed_index is None else seed_index
    if chosen not in indices:
        raise ValueError(f"seed_index {chosen} not in series (have {indices})")
    return series_df[series_df["snapshot_index"] == chosen].copy()


def _profile_by_distance(seed: pd.DataFrame) -> pd.DataFrame:
    work = seed.copy()
    work["distance_from_active"] = pd.to_numeric(work["distance_from_active"], errors="coerce").astype(int)
    work["x_amount_num"] = pd.to_numeric(work["x_amount"], errors="coerce").fillna(0)
    work["y_amount_num"] = pd.to_numeric(work["y_amount"], errors="coerce").fillna(0)
    work["liquidity_num"] = pd.to_numeric(work["liquidity"], errors="coerce").fillna(0)
    return (
        work.sort_values("distance_from_active")
        .drop_duplicates("distance_from_active", keep="first")
        .set_index("distance_from_active")
    )


def _composition_y(x_amount: float, y_amount: float) -> float | str:
    total = x_amount + y_amount
    if total <= 0:
        return ""
    return y_amount / total


def _roll_state(
    x_amount: np.ndarray,
    y_amount: np.ndarray,
    liquidity: np.ndarray,
    distances: np.ndarray,
    delta: int,
    template_x: np.ndarray,
    template_y: np.ndarray,
    template_liq: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Recenter liquidity arrays when the active bin moves by ``delta``."""
    if delta == 0:
        return x_amount.copy(), y_amount.copy(), liquidity.copy()

    dist_to_idx = {int(d): i for i, d in enumerate(distances)}
    x = np.zeros_like(x_amount)
    y = np.zeros_like(y_amount)
    liq = np.zeros_like(liquidity)

    for i, dist in enumerate(distances):
        src_dist = int(dist) + delta
        src_i = dist_to_idx.get(src_dist)
        if src_i is not None:
            x[i] = x_amount[src_i]
            y[i] = y_amount[src_i]
            liq[i] = liquidity[src_i]
        else:
            x[i] = template_x[i] * rng.uniform(0.92, 1.08)
            y[i] = template_y[i] * rng.uniform(0.92, 1.08)
            liq[i] = template_liq[i] * rng.uniform(0.95, 1.05)

    return x, y, liq


def _apply_swap_walk(
    x_amount: np.ndarray,
    y_amount: np.ndarray,
    liquidity: np.ndarray,
    distances: np.ndarray,
    delta: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Deplete offering-side shelves on bins crossed during the active-bin move."""
    if delta == 0:
        return x_amount, y_amount, liquidity

    x = x_amount.copy()
    y = y_amount.copy()
    liq = liquidity.copy()
    dist_to_idx = {int(d): i for i, d in enumerate(distances)}

    if delta > 0:
        for dist in range(1, delta + 1):
            idx = dist_to_idx.get(dist)
            if idx is None or x[idx] <= 0:
                continue
            take = rng.uniform(0.35, 0.92)
            removed = x[idx] * take
            x[idx] -= removed
            y[idx] += removed * rng.uniform(0.02, 0.12)
            liq[idx] *= 1.0 - take * rng.uniform(0.15, 0.45)
    else:
        for dist in range(-1, delta - 1, -1):
            idx = dist_to_idx.get(dist)
            if idx is None or y[idx] <= 0:
                continue
            take = rng.uniform(0.35, 0.92)
            removed = y[idx] * take
            y[idx] -= removed
            x[idx] += removed * rng.uniform(0.02, 0.12)
            liq[idx] *= 1.0 - take * rng.uniform(0.15, 0.45)

    return x, y, liq


def _price_for_bin_id(bin_id: int, ref_bin_id: int, ref_price: float) -> tuple[float, float]:
    step = 1.0 + BIN_STEP_BPS / 10_000.0
    price = ref_price * (step ** (bin_id - ref_bin_id))
    return price, price * 1000.0


def _apply_lp_refill(
    x_amount: np.ndarray,
    y_amount: np.ndarray,
    liquidity: np.ndarray,
    distances: np.ndarray,
    template_x: np.ndarray,
    template_y: np.ndarray,
    template_liq: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Slowly restore depleted bins toward the seed profile."""
    x = x_amount.copy()
    y = y_amount.copy()
    liq = liquidity.copy()

    for i, dist in enumerate(distances):
        target_x = template_x[i]
        target_y = template_y[i]
        target_liq = template_liq[i]

        if dist < 0 and target_y > 0 and y[i] < target_y * 0.85:
            y[i] += (target_y - y[i]) * rng.uniform(0.02, 0.09)
        elif dist > 0 and target_x > 0 and x[i] < target_x * 0.85:
            x[i] += (target_x - x[i]) * rng.uniform(0.02, 0.09)
        elif dist == 0:
            if x[i] < target_x * 0.7:
                x[i] += (target_x - x[i]) * rng.uniform(0.03, 0.12)
            if y[i] < target_y * 0.7:
                y[i] += (target_y - y[i]) * rng.uniform(0.03, 0.12)

        if liq[i] < target_liq * 0.8:
            liq[i] += (target_liq - liq[i]) * rng.uniform(0.02, 0.08)

    return x, y, liq


def _jitter_amounts(
    x_amount: np.ndarray,
    y_amount: np.ndarray,
    liquidity: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    noise = rng.uniform(0.985, 1.015, size=x_amount.shape)
    x = np.maximum(0.0, x_amount * noise)
    y = np.maximum(0.0, y_amount * rng.uniform(0.985, 1.015, size=y_amount.shape))
    liq = np.maximum(0.0, liquidity * rng.uniform(0.99, 1.01, size=liquidity.shape))
    return x, y, liq


def _build_snapshot_rows(
    *,
    snapshot_index: int,
    active_bin_id: int,
    profile: pd.DataFrame,
    x_amount: np.ndarray,
    y_amount: np.ndarray,
    liquidity: np.ndarray,
    pool_address: str,
    fetched_at: datetime,
    template_row: pd.Series,
    ref_bin_id: int,
    ref_price: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    for offset, dist in enumerate(profile.index.astype(int)):
        bin_id = active_bin_id + int(dist)
        row = profile.iloc[offset]
        x_val = max(0.0, float(x_amount[offset]))
        y_val = max(0.0, float(y_amount[offset]))
        liq_val = max(0.0, float(liquidity[offset]))
        price, price_per_token = _price_for_bin_id(bin_id, ref_bin_id, ref_price)

        rows.append(
            {
                "snapshot_index": snapshot_index,
                "pool_address": pool_address,
                "fetched_at_utc": fetched_at.isoformat().replace("+00:00", "Z"),
                "bin_array_index": int(row["bin_array_index"]) if pd.notna(row["bin_array_index"]) else "",
                "bin_id": bin_id,
                "distance_from_active": int(dist),
                "price": price,
                "price_per_token": price_per_token,
                "liquidity": int(round(liq_val)),
                "x_amount": int(round(x_val)),
                "y_amount": int(round(y_val)),
                "composition_y": _composition_y(x_val, y_val),
                "is_active_bin": dist == 0,
                "raw_bin_array_pubkey": template_row.get("raw_bin_array_pubkey", ""),
                "raw_fields_json": template_row.get("raw_fields_json", ""),
            }
        )

    return rows


def simulate_bin_atlas_series(
    seed_df: pd.DataFrame,
    *,
    snapshot_count: int,
    interval_sec: float = 10.0,
    start_time: datetime | None = None,
    active_deltas: tuple[int, ...] = DEFAULT_ACTIVE_DELTAS,
    rng: np.random.Generator | None = None,
) -> pd.DataFrame:
    """
    Evolve a seed snapshot into a longer series with swap-walk + LP-refill dynamics.

    Each step recenters the ±N bin neighborhood on the new active bin, depletes
    shelves when price moves, and slowly refills toward the seed profile.
    """
    if snapshot_count < 1:
        raise ValueError("snapshot_count must be >= 1")

    rng = rng or np.random.default_rng()
    profile = _profile_by_distance(seed_df)
    distances = profile.index.to_numpy(dtype=int)
    n_bins = len(distances)

    template_x = profile["x_amount_num"].to_numpy(dtype=np.float64)
    template_y = profile["y_amount_num"].to_numpy(dtype=np.float64)
    template_liq = profile["liquidity_num"].to_numpy(dtype=np.float64)

    active_bin_id = _active_bin_id(seed_df)
    ref_bin_id = active_bin_id
    ref_price = float(pd.to_numeric(seed_df.loc[seed_df["bin_id"] == ref_bin_id, "price"].iloc[0]))
    pool_address = str(seed_df["pool_address"].iloc[0])
    template_row = seed_df.iloc[0]
    start = start_time or datetime.now(tz=UTC)

    x_amount = template_x.copy()
    y_amount = template_y.copy()
    liquidity = template_liq.copy()

    all_rows: list[dict[str, object]] = []
    for snapshot_index in range(snapshot_count):
        fetched_at = start + timedelta(seconds=interval_sec * snapshot_index)
        all_rows.extend(
            _build_snapshot_rows(
                snapshot_index=snapshot_index,
                active_bin_id=active_bin_id,
                profile=profile,
                x_amount=x_amount,
                y_amount=y_amount,
                liquidity=liquidity,
                pool_address=pool_address,
                fetched_at=fetched_at,
                template_row=template_row,
                ref_bin_id=ref_bin_id,
                ref_price=ref_price,
            )
        )

        if snapshot_index + 1 >= snapshot_count:
            break

        delta = int(rng.choice(active_deltas))
        active_bin_id += delta
        x_amount, y_amount, liquidity = _roll_state(
            x_amount,
            y_amount,
            liquidity,
            distances,
            delta,
            template_x,
            template_y,
            template_liq,
            rng,
        )
        x_amount, y_amount, liquidity = _apply_swap_walk(
            x_amount, y_amount, liquidity, distances, delta, rng
        )
        x_amount, y_amount, liquidity = _apply_lp_refill(
            x_amount,
            y_amount,
            liquidity,
            distances,
            template_x,
            template_y,
            template_liq,
            rng,
        )
        x_amount, y_amount, liquidity = _jitter_amounts(x_amount, y_amount, liquidity, rng)

    return pd.DataFrame(all_rows)


def write_simulated_series_csv(
    df: pd.DataFrame,
    *,
    pool_address: str,
    processed_dir: Path = DATA_PROCESSED,
) -> Path:
    """Write simulated series CSV using the same naming convention as normalize:series."""
    processed_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
    path = processed_dir / f"bin_atlas_series_{pool_address}_{stamp}.csv"
    df.to_csv(path, index=False)
    return path


def build_simulated_series(
    pool_address: str | None = None,
    *,
    snapshot_count: int = 60,
    interval_sec: float = 10.0,
    seed_index: int | None = None,
    seed_csv: Path | None = None,
    processed_dir: Path = DATA_PROCESSED,
    rng_seed: int | None = None,
) -> tuple[pd.DataFrame, Path]:
    """Load a real seed series, simulate, and write CSV for render-mp4."""
    pool_address = pool_address or get_pool_address()
    rng = np.random.default_rng(rng_seed)

    if seed_csv is not None:
        series_df = pd.read_csv(seed_csv)
        if "fetched_at_utc" in series_df.columns:
            series_df["fetched_at_utc"] = pd.to_datetime(series_df["fetched_at_utc"], utc=True)
        seed_source = seed_csv
    else:
        series_df, seed_source = load_bin_atlas_series(pool_address, processed_dir=processed_dir)

    seed = _seed_snapshot(series_df, seed_index)
    simulated = simulate_bin_atlas_series(
        seed,
        snapshot_count=snapshot_count,
        interval_sec=interval_sec,
        rng=rng,
    )
    output_path = write_simulated_series_csv(simulated, pool_address=pool_address, processed_dir=processed_dir)

    print(f"Seed: {seed_source} (snapshot_index={seed['snapshot_index'].iloc[0]})")
    print(f"Wrote {output_path}")
    print(f"  {snapshot_count} simulated snapshots, {interval_sec}s apart, {len(simulated)} rows")
    return simulated, output_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate a long bin_atlas_series CSV from a real seed snapshot "
            "(for render-mp4 without RPC polling)."
        ),
    )
    parser.add_argument(
        "--pool",
        default=None,
        help=f"Pool address (default: METEORA_POOL_ADDRESS or {DEFAULT_POOL_ADDRESS}).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=60,
        help="Number of snapshots to generate (default: 60 → ~60s MP4 at FRAME_DURATION=1).",
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=10.0,
        help="Simulated seconds between snapshots (default: 10).",
    )
    parser.add_argument(
        "--seed-index",
        type=int,
        default=None,
        help="Snapshot index from the real series to seed from (default: last).",
    )
    parser.add_argument(
        "--seed-csv",
        type=Path,
        default=None,
        help="Explicit seed CSV (overrides --pool lookup).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for reproducible simulation.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    build_simulated_series(
        args.pool,
        snapshot_count=args.count,
        interval_sec=args.interval_sec,
        seed_index=args.seed_index,
        seed_csv=args.seed_csv,
        rng_seed=args.seed,
    )


if __name__ == "__main__":
    main()
