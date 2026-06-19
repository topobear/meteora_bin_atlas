"""Fetch interleaved snapshot series for all triangle legs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from meteora_bin_atlas.paths import PROJECT_ROOT
from meteora_bin_atlas.temporal.datasets import (
    DEFAULT_POLL_HZ,
    poll_interval_sec,
    resolve_rpc_dataset,
)
from meteora_bin_atlas.temporal.run import DEFAULT_SNAPSHOT_COUNT
from meteora_bin_atlas.temporal.simulate import build_simulated_series
from meteora_bin_atlas.temporal.triangle.resolve import TriangleSpec


@dataclass(frozen=True)
class TriangleFetchResult:
    manifest_path: Path | None
    leg_csv_paths: tuple[Path, Path, Path]


def _fetch_live_triangle(
    *,
    triangle_id: str,
    dataset: str,
    snapshot_count: int,
    rpc_backoff_sec: float,
    interval_sec: float,
    bins_left: int,
    bins_right: int,
    project_root: Path,
) -> None:
    rpc_dataset = resolve_rpc_dataset(dataset)
    env = os.environ.copy()
    env["SOLANA_RPC_URL"] = rpc_dataset.rpc_url

    cmd = [
        "npm",
        "run",
        "fetch:triangle-temporal",
        "--",
        "--triangle",
        triangle_id,
        "--dataset",
        dataset,
        "--count",
        str(snapshot_count),
        "--rpc-backoff-sec",
        str(rpc_backoff_sec),
        "--interval-sec",
        str(interval_sec),
        "--bins-left",
        str(bins_left),
        "--bins-right",
        str(bins_right),
    ]

    print(f"Fetching interleaved triangle series ({snapshot_count} snaps/leg)...")
    result = subprocess.run(cmd, cwd=project_root, env=env, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _latest_triangle_manifest(triangle_id: str, processed_dir: Path) -> Path:
    matches = sorted(processed_dir.glob(f"triangle_series_{triangle_id}_*.json"))
    if not matches:
        raise FileNotFoundError(
            f"No triangle manifest found for {triangle_id} in {processed_dir}",
        )
    return matches[-1]


def _latest_series_csv(pool_address: str, processed_dir: Path) -> Path:
    matches = [
        path
        for path in processed_dir.glob(f"bin_atlas_series_{pool_address}_*.csv")
        if not path.stem.endswith("_test")
    ]
    if not matches:
        raise FileNotFoundError(f"No series CSV found for pool {pool_address}")
    return max(matches, key=lambda path: path.stat().st_mtime)


def _csv_paths_from_manifest(manifest_path: Path, project_root: Path) -> tuple[Path, Path, Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    csv_paths: list[Path] = []
    processed_dir = project_root / "data" / "processed"

    for leg in manifest["legs"]:
        pool = leg["pool_address"]
        csv_paths.append(_latest_series_csv(pool, processed_dir))

    if len(csv_paths) != 3:
        raise ValueError(f"Expected 3 leg CSVs, found {len(csv_paths)}")
    return csv_paths[0], csv_paths[1], csv_paths[2]


def _seed_csv_for_pool(pool_address: str, processed_dir: Path) -> Path | None:
    matches = [
        path
        for path in processed_dir.glob(f"bin_atlas_series_{pool_address}_*.csv")
        if not path.stem.endswith("_test")
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def fetch_triangle_data(
    spec: TriangleSpec,
    *,
    dataset: str,
    snapshot_count: int = DEFAULT_SNAPSHOT_COUNT,
    poll_hz: float = DEFAULT_POLL_HZ,
    bins_left: int = 30,
    bins_right: int = 30,
    project_root: Path | None = None,
) -> TriangleFetchResult:
    """Fetch or simulate interleaved series for all triangle legs."""
    load_dotenv()
    project_root = project_root or PROJECT_ROOT
    processed_dir = project_root / "data" / "processed"
    interval_sec = poll_interval_sec(poll_hz)

    total_fetches = snapshot_count * 3
    print(
        f"Fetch triangle: {spec.triangle_id} dataset={dataset}\n"
        f"  {snapshot_count} snapshots/leg ({total_fetches} interleaved fetches) "
        f"@ {poll_hz:g} Hz (interval {interval_sec:.2f}s)"
    )
    print("")

    if dataset == "simulated":
        print("Source: simulated")
        leg_csvs: list[Path] = []
        for leg in spec.legs:
            seed_csv = _seed_csv_for_pool(leg.pool_address, processed_dir)
            kwargs = {
                "snapshot_count": snapshot_count,
                "interval_sec": interval_sec,
                "seed_dir": processed_dir,
                "simulated_dir": project_root / "data" / "simulated",
            }
            if seed_csv is not None:
                kwargs["seed_csv"] = seed_csv
            _, series_csv = build_simulated_series(leg.pool_address, **kwargs)
            leg_csvs.append(series_csv)
        return TriangleFetchResult(manifest_path=None, leg_csv_paths=(leg_csvs[0], leg_csvs[1], leg_csvs[2]))

    rpc_dataset = resolve_rpc_dataset(dataset, poll_hz=poll_hz)
    print(f"Source: {dataset} ({rpc_dataset.rpc_host})")
    _fetch_live_triangle(
        triangle_id=spec.triangle_id,
        dataset=dataset,
        snapshot_count=snapshot_count,
        rpc_backoff_sec=rpc_dataset.rpc_backoff_sec,
        interval_sec=rpc_dataset.interval_sec,
        bins_left=bins_left,
        bins_right=bins_right,
        project_root=project_root,
    )

    manifest_path = _latest_triangle_manifest(spec.triangle_id, processed_dir)
    leg_csvs = _csv_paths_from_manifest(manifest_path, project_root)
    return TriangleFetchResult(manifest_path=manifest_path, leg_csv_paths=leg_csvs)
