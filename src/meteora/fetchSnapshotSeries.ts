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
import type { BinArraysFetchResult } from "./types.js";

export type SnapshotSeriesEntry = {
  index: number;
  fetched_at_utc: string;
  raw_filename: string;
  active_bin_id?: number;
  bin_count?: number;
};

export type SnapshotSeriesManifest = {
  pool_address: string;
  series_started_at_utc: string;
  series_completed_at_utc: string;
  interval_sec: number;
  /** Conservative RPC backoff applied after each successful snapshot, before interval. */
  rpc_backoff_sec: number;
  snapshot_count: number;
  bounded?: {
    left: number;
    right: number;
  };
  snapshots: SnapshotSeriesEntry[];
};

export type FetchSnapshotSeriesOptions = {
  count: number;
  /** Extra seconds to wait after RPC backoff before the next snapshot. */
  intervalSec: number;
  /** Conservative RPC backoff after each successful snapshot (applied before interval). */
  rpcBackoffSec?: number;
  projectRoot: string;
  dataset?: DatasetId;
  bounded: {
    left: number;
    right: number;
  };
};

/** Backoff when a snapshot RPC call fails (conservative, applied before retry). */
const RETRY_BACKOFF_SEC = [15, 45, 90, 180];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class RpcDatasetAbortError extends Error {
  readonly dataset: DatasetId;

  constructor(dataset: DatasetId, cause: unknown) {
    super(formatRpcDatasetWarning(cause, dataset));
    this.name = "RpcDatasetAbortError";
    this.dataset = dataset;
  }
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

export async function fetchSnapshotSeries(
  connection: Connection,
  poolAddress: string,
  options: FetchSnapshotSeriesOptions,
): Promise<SnapshotSeriesManifest> {
  const seriesStartedAtUtc = new Date().toISOString();
  const snapshots: SnapshotSeriesEntry[] = [];
  const rpcBackoffSec = options.rpcBackoffSec ?? 60;
  const dataset = options.dataset ?? "alchemy";
  const { left, right } = options.bounded;

  console.log(`Source: ${dataset} (Solana RPC)`);

  let dlmmPool: Awaited<ReturnType<typeof DLMM.create>>;
  try {
    dlmmPool = await DLMM.create(connection, new PublicKey(poolAddress));
  } catch (error: unknown) {
    if (isRpcDatasetError(error)) {
      throw new RpcDatasetAbortError(dataset, error);
    }
    throw error;
  }

  for (let index = 0; index < options.count; index += 1) {
    const result = await fetchBoundedSnapshotWithRetry(
      dlmmPool,
      poolAddress,
      left,
      right,
      dataset,
    );
    const { rawFilename } = await writeSnapshotRaw(options.projectRoot, poolAddress, result);

    snapshots.push({
      index,
      fetched_at_utc: result.fetched_at_utc,
      raw_filename: rawFilename,
      active_bin_id: result.summary?.active_bin_id,
      bin_count: result.summary?.bin_count,
    });

    console.log(
      `Snapshot ${index + 1}/${options.count}: active_bin=${result.summary?.active_bin_id ?? "?"}`,
    );

    if (index < options.count - 1) {
      await waitBeforeNextSnapshot(rpcBackoffSec, options.intervalSec);
    }
  }

  return {
    pool_address: poolAddress,
    series_started_at_utc: seriesStartedAtUtc,
    series_completed_at_utc: new Date().toISOString(),
    interval_sec: options.intervalSec,
    rpc_backoff_sec: rpcBackoffSec,
    snapshot_count: options.count,
    bounded: options.bounded,
    snapshots,
  };
}
