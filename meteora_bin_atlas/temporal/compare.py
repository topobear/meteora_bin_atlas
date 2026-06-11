"""Compare simulated bin-atlas dynamics against real RPC-fetched series."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from meteora_bin_atlas.config import DEFAULT_POOL_ADDRESS, get_pool_address
from meteora_bin_atlas.paths import DATA_PROCESSED, DATA_SIMULATED
from meteora_bin_atlas.temporal.calibrate import active_bin_deltas, estimate_poll_interval_sec
from meteora_bin_atlas.temporal.calibrate import calibrate_from_real_series
from meteora_bin_atlas.temporal.load import load_real_bin_atlas_series, load_simulated_bin_atlas_series
from meteora_bin_atlas.temporal.simulate import _seed_snapshot, simulate_bin_atlas_series


@dataclass(frozen=True)
class DynamicsMetrics:
    n_snapshots: int
    poll_interval_sec: float
    active_delta_mean: float
    active_delta_std: float
    active_abs_delta_mean: float
    p_stationary: float
    p_large_move: float
    active_range_bins: int
    delta_autocorr_lag1: float
    d_liquidity_pct_mean: float
    d_liquidity_pct_std: float
    d_imbalance_std: float
    profile_corr_mean: float
    profile_l2_mean: float


def _active_bin_id(group: pd.DataFrame) -> int:
    mask = group["is_active_bin"].astype(str).str.lower().eq("true")
    if not mask.any():
        mask = group["is_active_bin"] == True  # noqa: E712
    return int(group.loc[mask, "bin_id"].iloc[0])


def compute_dynamics_metrics(df: pd.DataFrame) -> DynamicsMetrics:
    """Summarize temporal motion and profile stability for a bin-atlas series."""
    work = df.copy()
    work["liquidity_num"] = pd.to_numeric(work["liquidity"], errors="coerce").fillna(0)
    work["dist"] = pd.to_numeric(work["distance_from_active"], errors="coerce").astype(int)

    poll_interval = estimate_poll_interval_sec(work) if "fetched_at_utc" in work.columns else float("nan")

    snaps: list[dict] = []
    for snapshot_index, group in work.groupby("snapshot_index"):
        active = group.loc[group["is_active_bin"].astype(str).str.lower().eq("true") | (group["is_active_bin"] == True)]
        total = float(group["liquidity_num"].sum())
        left = float(group.loc[group["dist"] < 0, "liquidity_num"].sum())
        right = float(group.loc[group["dist"] > 0, "liquidity_num"].sum())
        snaps.append(
            {
                "snapshot_index": int(snapshot_index),
                "active_bin_id": _active_bin_id(group),
                "liquidity_total": total,
                "imbalance": (right - left) / total if total > 0 else 0.0,
                "profile": group.set_index("dist")[["liquidity_num"]].sort_index(),
            }
        )
    snaps.sort(key=lambda row: row["snapshot_index"])

    deltas = active_bin_deltas(work).astype(float)
    d_liq: list[float] = []
    d_imb: list[float] = []
    corr: list[float] = []
    l2: list[float] = []

    for i in range(1, len(snaps)):
        prev, cur = snaps[i - 1], snaps[i]
        if prev["liquidity_total"] > 0:
            d_liq.append((cur["liquidity_total"] - prev["liquidity_total"]) / prev["liquidity_total"])
        d_imb.append(cur["imbalance"] - prev["imbalance"])
        common = prev["profile"].index.intersection(cur["profile"].index)
        if len(common) > 3:
            p = prev["profile"].loc[common, "liquidity_num"].to_numpy(dtype=float)
            c = cur["profile"].loc[common, "liquidity_num"].to_numpy(dtype=float)
            if p.std() > 0 and c.std() > 0:
                corr.append(float(np.corrcoef(p, c)[0, 1]))
            l2.append(float(np.linalg.norm(c - p) / (np.linalg.norm(p) + 1e-9)))

    active_bins = np.array([row["active_bin_id"] for row in snaps], dtype=float)
    ac = float(np.corrcoef(deltas[:-1], deltas[1:])[0, 1]) if len(deltas) > 2 else float("nan")

    return DynamicsMetrics(
        n_snapshots=len(snaps),
        poll_interval_sec=poll_interval,
        active_delta_mean=float(deltas.mean()) if len(deltas) else 0.0,
        active_delta_std=float(deltas.std()) if len(deltas) else 0.0,
        active_abs_delta_mean=float(np.abs(deltas).mean()) if len(deltas) else 0.0,
        p_stationary=float(np.mean(deltas == 0)) if len(deltas) else 1.0,
        p_large_move=float(np.mean(np.abs(deltas) >= 2)) if len(deltas) else 0.0,
        active_range_bins=int(active_bins.max() - active_bins.min()) if len(active_bins) else 0,
        delta_autocorr_lag1=ac,
        d_liquidity_pct_mean=float(np.mean(d_liq) * 100) if d_liq else 0.0,
        d_liquidity_pct_std=float(np.std(d_liq) * 100) if d_liq else 0.0,
        d_imbalance_std=float(np.std(d_imb)) if d_imb else 0.0,
        profile_corr_mean=float(np.mean(corr)) if corr else 1.0,
        profile_l2_mean=float(np.mean(l2)) if l2 else 0.0,
    )


def _pct_delta(real: float, sim: float) -> float:
    if real == 0:
        return float("inf") if sim != 0 else 0.0
    return (sim - real) / abs(real) * 100.0


def format_metrics(name: str, metrics: DynamicsMetrics) -> str:
    lines = [
        f"=== {name} ({metrics.n_snapshots} snapshots, poll≈{metrics.poll_interval_sec:.2f}s) ===",
        (
            f"  Active bin Δ: mean={metrics.active_delta_mean:+.3f} "
            f"std={metrics.active_delta_std:.3f} |Δ|={metrics.active_abs_delta_mean:.3f}"
        ),
        (
            f"    P(stationary)={metrics.p_stationary:.1%} "
            f"P(|Δ|≥2)={metrics.p_large_move:.1%} range={metrics.active_range_bins} bins"
        ),
        f"  Δliquidity_total: mean={metrics.d_liquidity_pct_mean:+.3f}% std={metrics.d_liquidity_pct_std:.3f}%",
        f"  Profile corr={metrics.profile_corr_mean:.4f} L2 rel={metrics.profile_l2_mean:.4f}",
        f"  Delta autocorr lag-1={metrics.delta_autocorr_lag1:+.3f}",
    ]
    return "\n".join(lines)


def subsample_series_to_interval(series_df: pd.DataFrame, target_interval_sec: float) -> pd.DataFrame:
    """Subsample a high-frequency real series to approximate ``target_interval_sec`` steps."""
    poll = estimate_poll_interval_sec(series_df)
    stride = max(1, int(round(target_interval_sec / poll)))
    keep = sorted(series_df["snapshot_index"].unique())[::stride]
    sub = series_df[series_df["snapshot_index"].isin(keep)].copy()
    mapping = {old: new for new, old in enumerate(keep)}
    sub["snapshot_index"] = sub["snapshot_index"].map(mapping)
    return sub


def format_comparison(real: DynamicsMetrics, sim: DynamicsMetrics) -> str:
    checks = [
        ("|Δ| mean", real.active_abs_delta_mean, sim.active_abs_delta_mean, 0.8),
        ("P(stationary)", real.p_stationary, sim.p_stationary, 0.25),
        ("active range", float(real.active_range_bins), float(sim.active_range_bins), 1.5),
        ("Δliq std %", real.d_liquidity_pct_std, sim.d_liquidity_pct_std, 1.0),
        ("profile L2", real.profile_l2_mean, sim.profile_l2_mean, 1.0),
    ]
    lines = ["=== Interval-matched real vs simulated (relative error) ==="]
    for label, r, s, tol in checks:
        err = _pct_delta(r, s)
        flag = "OK" if abs(err) <= tol * 100 else "HIGH"
        lines.append(f"  {label:16s} real={r:8.4f}  sim={s:8.4f}  err={err:+6.1f}%  [{flag}]")
    return "\n".join(lines)


def compare_simulated_to_real(
    pool_address: str,
    *,
    interval_sec: float = 10.0,
    snapshot_count: int | None = None,
    motion_scale: float = 0.75,
    rng_seed: int = 42,
    processed_dir=DATA_PROCESSED,
    simulated_dir=DATA_SIMULATED,
    resimulate: bool = True,
) -> tuple[DynamicsMetrics, DynamicsMetrics, str]:
    """Compare fresh or latest simulated output against the latest real series."""
    real_df, _ = load_real_bin_atlas_series(pool_address, processed_dir=processed_dir)
    real_metrics = compute_dynamics_metrics(real_df)
    real_at_interval = compute_dynamics_metrics(subsample_series_to_interval(real_df, interval_sec))

    if resimulate:
        seed = _seed_snapshot(real_df, None)
        count = snapshot_count or real_metrics.n_snapshots
        calibration = calibrate_from_real_series(
            real_df,
            pool_address=pool_address,
            interval_sec=interval_sec,
            motion_scale=motion_scale,
        )
        sim_df = simulate_bin_atlas_series(
            seed,
            snapshot_count=count,
            interval_sec=interval_sec,
            motion_scale=motion_scale,
            calibration=calibration,
            pool_address=pool_address,
            rng=np.random.default_rng(rng_seed),
        )
    else:
        sim_df, _ = load_simulated_bin_atlas_series(pool_address, simulated_dir=simulated_dir)

    sim_metrics = compute_dynamics_metrics(sim_df)
    report = "\n\n".join(
        [
            format_metrics("REAL (native poll)", real_metrics),
            format_metrics(f"REAL (~{interval_sec:g}s subsample)", real_at_interval),
            format_metrics("SIMULATED", sim_metrics),
            format_comparison(real_at_interval, sim_metrics),
        ]
    )
    return real_metrics, sim_metrics, report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare simulated vs real bin-atlas dynamics.")
    parser.add_argument("--pool", default=None, help=f"Pool address (default env or {DEFAULT_POOL_ADDRESS}).")
    parser.add_argument("--interval-sec", type=float, default=10.0, help="Simulated step interval.")
    parser.add_argument("--count", type=int, default=None, help="Snapshots to simulate (default: match real).")
    parser.add_argument(
        "--motion-scale",
        type=float,
        default=0.75,
        help="0=empirical motion, 1=maximum demo drift (default 0.75).",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for resimulation.")
    parser.add_argument(
        "--latest-file",
        action="store_true",
        help="Compare the latest on-disk simulated CSV instead of resimulating.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    _, _, report = compare_simulated_to_real(
        args.pool or get_pool_address(),
        interval_sec=args.interval_sec,
        snapshot_count=args.count,
        motion_scale=args.motion_scale,
        rng_seed=args.seed,
        resimulate=not args.latest_file,
    )
    print(report)


if __name__ == "__main__":
    main()
