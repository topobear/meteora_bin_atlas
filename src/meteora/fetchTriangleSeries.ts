import path from "node:path";

import DLMM from "@meteora-ag/dlmm";
import { Connection, PublicKey } from "@solana/web3.js";

import { writeJson } from "../io/writeJson.js";
import type { DatasetId } from "../datasets.js";
import {
  formatRpcDatasetWarning,
  isRpcDatasetError,
  rpcErrorMessage,
} from "../rpcErrors.js";
import { formatTimestampForFilename } from "./discoverPools.js";
import { fetchBoundedBinsFromPool } from "./fetchBinArrays.js";
import type { SnapshotSeriesEntry, SnapshotSeriesManifest } from "./fetchSnapshotSeries.js";
import { RpcDatasetAbortError } from "./fetchSnapshotSeries.js";
import type { BinArraysFetchResult } from "./types.js";

export type TriangleLegInput = {
  leg_index: number;
  leg_key: string;
  pool_address: string;
};

export type TriangleLegManifest = {
  leg_index: number;
  leg_key: string;
  pool_address: string;
  snapshots: SnapshotSeriesEntry[];
};

export type TriangleSeriesManifest = {
  triangle_id: string;
  series_started_at_utc: string;
  series_completed_at_utc: string;
  interval_sec: number;
  rpc_backoff_sec: number;
  snapshot_count_per_leg: number;
  total_fetches: number;
  interleaved: true;
  bounded: {
    left: number;
    right: number;
  };
  legs: TriangleLegManifest[];
};

export type FetchTriangleSeriesOptions = {
  triangleId: string;
  legs: TriangleLegInput[];
  countPerLeg: number;
  intervalSec: number;
  rpcBackoffSec?: number;
  projectRoot: string;
  dataset?: DatasetId;
  bounded: {
    left: number;
    right: number;
  };
};

const RETRY_BACKOFF_SEC = [15, 45, 90, 180];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatFetchError(error: unknown, dataset: DatasetId): string {
  if (isRpcDatasetError(error)) {
    return formatRpcDatasetWarning(error, dataset);
  }
  return rpcErrorMessage(error);
}

async function fetchBoundedSnapshotWithRetry(
  dlmmPool: Awaited<ReturnType<typeof DLMM.create>>,
  poolAddress: string,
  left: number,
  right: number,
  dataset: DatasetId,
  maxAttempts = RETRY_BACKOFF_SEC.length,
): Promise<BinArraysFetchResult> {
  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fetchBoundedBinsFromPool(dlmmPool, poolAddress, left, right);
    } catch (error: unknown) {
      lastError = error;

      if (isRpcDatasetError(error)) {
        throw new RpcDatasetAbortError(dataset, error);
      }

      if (attempt === maxAttempts) {
        break;
      }

      const waitSec = RETRY_BACKOFF_SEC[attempt - 1] ?? 180;
      console.warn(
        `Snapshot fetch failed (attempt ${attempt}/${maxAttempts}): ${formatFetchError(error, dataset)}`,
      );
      console.warn(`Retry backoff ${waitSec}s...`);
      await sleep(waitSec * 1000);
    }
  }

  throw lastError;
}

async function writeSnapshotRaw(
  projectRoot: string,
  poolAddress: string,
  result: BinArraysFetchResult,
): Promise<{ rawFilename: string; rawPath: string }> {
  const timestamp = formatTimestampForFilename(new Date(result.fetched_at_utc));
  const rawFilename = `bin_arrays_${poolAddress}_${timestamp}.json`;
  const rawPath = path.join(projectRoot, "data", "raw", rawFilename);

  await writeJson(rawPath, {
    pool_address: result.pool_address,
    fetched_at_utc: result.fetched_at_utc,
    method: result.method,
    summary: result.summary,
    raw: result.raw,
  });

  return { rawFilename, rawPath };
}

async function waitBeforeNextSnapshot(
  rpcBackoffSec: number,
  intervalSec: number,
): Promise<void> {
  if (rpcBackoffSec > 0) {
    console.log(`RPC backoff ${rpcBackoffSec}s (rate-limit cushion)...`);
    await sleep(rpcBackoffSec * 1000);
  }

  if (intervalSec > 0) {
    console.log(`Interval ${intervalSec}s before next snapshot...`);
    await sleep(intervalSec * 1000);
  }
}

export async function fetchTriangleSeries(
  connection: Connection,
  options: FetchTriangleSeriesOptions,
): Promise<TriangleSeriesManifest> {
  const seriesStartedAtUtc = new Date().toISOString();
  const rpcBackoffSec = options.rpcBackoffSec ?? 60;
  const dataset = options.dataset ?? "alchemy";
  const { left, right } = options.bounded;
  const sortedLegs = [...options.legs].sort((a, b) => a.leg_index - b.leg_index);
  const totalFetches = options.countPerLeg * sortedLegs.length;

  const legSnapshots: TriangleLegManifest[] = sortedLegs.map((leg) => ({
    leg_index: leg.leg_index,
    leg_key: leg.leg_key,
    pool_address: leg.pool_address,
    snapshots: [],
  }));

  const poolAddresses = sortedLegs.map((leg) => new PublicKey(leg.pool_address));
  let dlmmPools: Awaited<ReturnType<typeof DLMM.createMultiple>>;
  try {
    dlmmPools = await DLMM.createMultiple(connection, poolAddresses);
  } catch (error: unknown) {
    if (isRpcDatasetError(error)) {
      throw new RpcDatasetAbortError(dataset, error);
    }
    throw error;
  }

  const poolByAddress = new Map<string, Awaited<ReturnType<typeof DLMM.create>>>();
  for (let i = 0; i < sortedLegs.length; i += 1) {
    poolByAddress.set(sortedLegs[i].pool_address, dlmmPools[i]);
  }

  for (let fetchIndex = 0; fetchIndex < totalFetches; fetchIndex += 1) {
    const legSlot = fetchIndex % sortedLegs.length;
    const leg = sortedLegs[legSlot];
    const legManifest = legSnapshots[legSlot];
    const snapshotIndex = legManifest.snapshots.length;
    const dlmmPool = poolByAddress.get(leg.pool_address);
    if (!dlmmPool) {
      throw new Error(`DLMM pool instance missing for ${leg.pool_address}`);
    }

    const result = await fetchBoundedSnapshotWithRetry(
      dlmmPool,
      leg.pool_address,
      left,
      right,
      dataset,
    );
    const { rawFilename } = await writeSnapshotRaw(options.projectRoot, leg.pool_address, result);

    legManifest.snapshots.push({
      index: snapshotIndex,
      fetched_at_utc: result.fetched_at_utc,
      raw_filename: rawFilename,
      active_bin_id: result.summary?.active_bin_id,
      bin_count: result.summary?.bin_count,
    });

    console.log(
      `Fetch ${fetchIndex + 1}/${totalFetches} leg ${leg.leg_key} snap ${snapshotIndex + 1}/${options.countPerLeg}: active_bin=${result.summary?.active_bin_id ?? "?"}`,
    );

    if (fetchIndex < totalFetches - 1) {
      await waitBeforeNextSnapshot(rpcBackoffSec, options.intervalSec);
    }
  }

  return {
    triangle_id: options.triangleId,
    series_started_at_utc: seriesStartedAtUtc,
    series_completed_at_utc: new Date().toISOString(),
    interval_sec: options.intervalSec,
    rpc_backoff_sec: rpcBackoffSec,
    snapshot_count_per_leg: options.countPerLeg,
    total_fetches: totalFetches,
    interleaved: true,
    bounded: options.bounded,
    legs: legSnapshots,
  };
}

export function legSeriesManifest(
  triangleManifest: TriangleSeriesManifest,
  leg: TriangleLegManifest,
): SnapshotSeriesManifest {
  return {
    pool_address: leg.pool_address,
    series_started_at_utc: triangleManifest.series_started_at_utc,
    series_completed_at_utc: triangleManifest.series_completed_at_utc,
    interval_sec: triangleManifest.interval_sec,
    rpc_backoff_sec: triangleManifest.rpc_backoff_sec,
    snapshot_count: leg.snapshots.length,
    bounded: triangleManifest.bounded,
    snapshots: leg.snapshots,
  };
}
