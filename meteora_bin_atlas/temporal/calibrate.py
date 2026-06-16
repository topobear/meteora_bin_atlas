"""Calibrate simulation dynamics from real bin-atlas series and OHLCV."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from meteora_bin_atlas.temporal.datasets import DEFAULT_POLL_HZ, poll_interval_sec
from meteora_bin_atlas.temporal.load import load_pool_ohlcv, load_real_bin_atlas_series

BIN_STEP_BPS = 4
LOG_BIN_STEP = math.log(1.0 + BIN_STEP_BPS / 10_000.0)

TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(frozen=True)
class ActiveBinCalibration:
    """Brownian active-bin step scaled to ``interval_sec``."""

    interval_sec: float
    reference_interval_sec: float
    sigma_bins_native: float
    sigma_bins_step: float
    momentum: float
    empirical_move_rate: float
    p_stay: float
    motion_scale: float
    velocity_stick_threshold: float


@dataclass(frozen=True)
class LiquidityCalibration:
    """Ornstein–Uhlenbeck noise on per-bin amounts."""

    ou_kappa: float
    stationary_rel_sigma: float
    moving_rel_sigma: float
    jitter_span: float
    swap_intensity: float


@dataclass(frozen=True)
class SimulationCalibration:
    active_bin: ActiveBinCalibration
    liquidity: LiquidityCalibration


def _active_bin_id(group: pd.DataFrame) -> int:
    mask = group["is_active_bin"].astype(str).str.lower().eq("true")
    if not mask.any():
        mask = group["is_active_bin"] == True  # noqa: E712
    return int(group.loc[mask, "bin_id"].iloc[0])


def estimate_poll_interval_sec(series_df: pd.DataFrame) -> float:
    """Median seconds between consecutive snapshots in a real series."""
    snaps = series_df.groupby("snapshot_index")["fetched_at_utc"].first().sort_index()
    if pd.api.types.is_string_dtype(snaps):
        snaps = pd.to_datetime(snaps, utc=True)
    deltas = snaps.diff().dt.total_seconds().dropna()
    if deltas.empty:
        return poll_interval_sec(DEFAULT_POLL_HZ)
    return float(deltas.median())


def active_bin_deltas(series_df: pd.DataFrame) -> np.ndarray:
    """Per-step active-bin increments ordered by snapshot index."""
    active_by_snap: dict[int, int] = {}
    for snapshot_index, group in series_df.groupby("snapshot_index"):
        active_by_snap[int(snapshot_index)] = _active_bin_id(group)

    indices = sorted(active_by_snap)
    if len(indices) < 2:
        return np.array([], dtype=int)

    return np.array(
        [active_by_snap[indices[i]] - active_by_snap[indices[i - 1]] for i in range(1, len(indices))],
        dtype=int,
    )


def empirical_active_bin_sigma_bins(
    series_df: pd.DataFrame,
    *,
    reference_interval_sec: float | None = None,
) -> tuple[float, float]:
    """
    Return (sigma_bins_native, move_rate) from a real series.

    ``sigma_bins_native`` is the per-poll standard deviation of bin increments.
    """
    _ = reference_interval_sec
    deltas = active_bin_deltas(series_df).astype(float)
    if len(deltas) == 0:
        return 0.3, 0.04

    sigma = float(deltas.std())
    move_rate = float(np.mean(deltas != 0))
    return sigma, move_rate


def ohlcv_sigma_bins(
    pool_address: str,
    *,
    interval_sec: float,
    processed_dir=None,
) -> float:
    """Brownian bin-step volatility implied by pool OHLCV candles."""
    kwargs = {} if processed_dir is None else {"processed_dir": processed_dir}
    try:
        ohlcv, meta = load_pool_ohlcv(pool_address, **kwargs)
    except FileNotFoundError:
        return 0.5
    if ohlcv.empty or len(ohlcv) < 2:
        return 0.5

    timeframe = str(meta.get("timeframe", "1h"))
    candle_sec = TIMEFRAME_SECONDS.get(timeframe, 3600)
    rets = np.log(ohlcv["close"] / ohlcv["close"].shift(1)).dropna()
    sigma_per_sec = float(rets.std()) / math.sqrt(candle_sec)
    sigma_log = sigma_per_sec * math.sqrt(interval_sec)
    return sigma_log / LOG_BIN_STEP


def liquidity_step_stats(series_df: pd.DataFrame) -> tuple[float, float]:
    """
    Per-bin relative liquidity change std when the active bin is stationary vs moving.
    """
    work = series_df.copy()
    work["liq"] = pd.to_numeric(work["liquidity"], errors="coerce").fillna(0)
    work["dist"] = pd.to_numeric(work["distance_from_active"], errors="coerce").astype(int)

    active_by_snap: dict[int, int] = {}
    for snapshot_index, group in work.groupby("snapshot_index"):
        active_by_snap[int(snapshot_index)] = _active_bin_id(group)

    indices = sorted(active_by_snap)
    stationary: list[float] = []
    moving: list[float] = []

    for i in range(1, len(indices)):
        prev_si, cur_si = indices[i - 1], indices[i]
        delta_bin = active_by_snap[cur_si] - active_by_snap[prev_si]
        prev_g = work[work["snapshot_index"] == prev_si].set_index("dist")["liq"]
        cur_g = work[work["snapshot_index"] == cur_si].set_index("dist")["liq"]
        common = prev_g.index.intersection(cur_g.index)
        if len(common) == 0:
            continue
        denom = prev_g.loc[common].to_numpy(dtype=float) + 1.0
        rel = (cur_g.loc[common].to_numpy(dtype=float) - prev_g.loc[common].to_numpy(dtype=float)) / denom
        if delta_bin == 0:
            stationary.extend(rel.tolist())
        else:
            moving.extend(rel.tolist())

    stat_sigma = float(np.std(stationary)) if stationary else 1e-4
    move_sigma = float(np.std(moving)) if moving else 0.05
    return stat_sigma, move_sigma


def calibrate_from_real_series(
    series_df: pd.DataFrame,
    *,
    pool_address: str,
    interval_sec: float,
    motion_scale: float = 0.75,
    momentum: float | None = None,
) -> SimulationCalibration:
    """
    Blend empirical active-bin motion with OHLCV-implied Brownian volatility.

    ``motion_scale=0`` tracks the real poll closely; ``1`` is maximum demo drift.
    Default ``0.75`` prioritises visible MP4 motion while staying below legacy iid chaos.
    """
    motion_scale = max(0.0, min(1.0, motion_scale))
    poll = estimate_poll_interval_sec(series_df)
    emp_sigma, move_rate = empirical_active_bin_sigma_bins(series_df)
    ohlcv_sigma = ohlcv_sigma_bins(pool_address, interval_sec=interval_sec)
    dt_ratio = max(interval_sec / poll, 1e-6)
    emp_step = emp_sigma * math.sqrt(dt_ratio)
    ohlcv_step = ohlcv_sigma
    sigma_step = (1.0 - motion_scale) * emp_step + motion_scale * ohlcv_step
    sigma_step *= 1.0 + 1.25 * motion_scale
    sigma_step = max(sigma_step, 0.05)

    p_stay_empirical = (1.0 - move_rate) ** dt_ratio
    # Compress stay probability so higher motion_scale unlocks more bin crossings.
    demo_dt_mult = 1.0 + 18.0 * motion_scale
    p_stay_demo = (1.0 - move_rate) ** (dt_ratio * demo_dt_mult)
    p_stay = (1.0 - motion_scale) * p_stay_empirical + motion_scale * p_stay_demo
    p_stay = max(0.28, min(0.995, p_stay))

    if momentum is None:
        momentum = 0.10 + 0.38 * motion_scale

    stat_sigma, move_sigma = liquidity_step_stats(series_df)
    liq_scale = math.sqrt(min(dt_ratio, 4.0))
    liq_motion = 1.0 + 2.5 * motion_scale

    return SimulationCalibration(
        active_bin=ActiveBinCalibration(
            interval_sec=interval_sec,
            reference_interval_sec=poll,
            sigma_bins_native=emp_sigma,
            sigma_bins_step=sigma_step,
            momentum=momentum,
            empirical_move_rate=move_rate,
            p_stay=p_stay,
            motion_scale=motion_scale,
            velocity_stick_threshold=0.35 * (1.0 - 0.75 * motion_scale),
        ),
        liquidity=LiquidityCalibration(
            ou_kappa=0.18 + 0.12 * motion_scale,
            stationary_rel_sigma=max(stat_sigma * liq_scale * liq_motion, 1e-5),
            moving_rel_sigma=max(
                move_sigma * math.sqrt(liq_scale) * (0.35 + 0.55 * motion_scale),
                stat_sigma * (1.5 + motion_scale),
            ),
            jitter_span=0.002 + 0.006 * motion_scale,
            swap_intensity=0.35 + 0.45 * motion_scale,
        ),
    )


def load_default_calibration(
    pool_address: str,
    *,
    interval_sec: float,
    processed_dir=None,
    motion_scale: float = 0.75,
) -> SimulationCalibration:
    """Load the latest real series for a pool and calibrate simulation dynamics."""
    kwargs = {} if processed_dir is None else {"processed_dir": processed_dir}
    series_df, _ = load_real_bin_atlas_series(pool_address, **kwargs)
    return calibrate_from_real_series(
        series_df,
        pool_address=pool_address,
        interval_sec=interval_sec,
        motion_scale=motion_scale,
    )
