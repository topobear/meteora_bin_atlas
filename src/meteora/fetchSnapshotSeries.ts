import path from "node:path";

import { Connection } from "@solana/web3.js";

import { writeJson } from "../io/writeJson.js";
import { formatTimestampForFilename } from "./discoverPools.js";
import { fetchBinArrays, type FetchBinArraysOptions } from "./fetchBinArrays.js";
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
  snapshot_count: number;
  bounded?: {
    left: number;
    right: number;
  };
  snapshots: SnapshotSeriesEntry[];
};

export type FetchSnapshotSeriesOptions = {
  count: number;
  intervalSec: number;
  projectRoot: string;
  fetchOptions?: FetchBinArraysOptions;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchBinArraysWithRetry(
  connection: Connection,
  poolAddress: string,
  fetchOptions?: FetchBinArraysOptions,
  maxAttempts = 4,
): Promise<BinArraysFetchResult> {
  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fetchBinArrays(connection, poolAddress, fetchOptions);
    } catch (error: unknown) {
      lastError = error;
      if (attempt === maxAttempts) {
        break;
      }

      const waitMs = attempt * 3000;
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`Snapshot fetch failed (attempt ${attempt}/${maxAttempts}): ${message}`);
      console.warn(`Retrying in ${waitMs / 1000}s...`);
      await sleep(waitMs);
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

export async function fetchSnapshotSeries(
  connection: Connection,
  poolAddress: string,
  options: FetchSnapshotSeriesOptions,
): Promise<SnapshotSeriesManifest> {
  const seriesStartedAtUtc = new Date().toISOString();
  const snapshots: SnapshotSeriesEntry[] = [];

  for (let index = 0; index < options.count; index += 1) {
    const result = await fetchBinArraysWithRetry(
      connection,
      poolAddress,
      options.fetchOptions,
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
      await sleep(options.intervalSec * 1000);
    }
  }

  return {
    pool_address: poolAddress,
    series_started_at_utc: seriesStartedAtUtc,
    series_completed_at_utc: new Date().toISOString(),
    interval_sec: options.intervalSec,
    snapshot_count: options.count,
    bounded: options.fetchOptions?.bounded,
    snapshots,
  };
}
