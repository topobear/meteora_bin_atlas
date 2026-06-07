import path from "node:path";

import DLMM from "@meteora-ag/dlmm";
import { Connection, PublicKey } from "@solana/web3.js";

import { writeJson } from "../io/writeJson.js";
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
  cooldown_sec: number;
  snapshot_count: number;
  bounded?: {
    left: number;
    right: number;
  };
  snapshots: SnapshotSeriesEntry[];
};

export type FetchSnapshotSeriesOptions = {
  count: number;
  /** Seconds to wait between snapshots (after cooldown). */
  intervalSec: number;
  /** Seconds to pause after each successful snapshot before interval wait. */
  cooldownSec?: number;
  projectRoot: string;
  bounded: {
    left: number;
    right: number;
  };
};

const RETRY_BACKOFF_SEC = [10, 30, 60, 120];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatFetchError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const lower = message.toLowerCase();

  if (
    message === "fetch failed" ||
    lower.includes("429") ||
    lower.includes("too many") ||
    lower.includes("rate limit")
  ) {
    return `${message} (likely Solana RPC rate limit — slow down with --interval-sec / --cooldown-sec, or use a private RPC)`;
  }

  return message;
}

async function fetchBoundedSnapshotWithRetry(
  dlmmPool: Awaited<ReturnType<typeof DLMM.create>>,
  poolAddress: string,
  left: number,
  right: number,
  maxAttempts = RETRY_BACKOFF_SEC.length,
): Promise<BinArraysFetchResult> {
  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      return await fetchBoundedBinsFromPool(dlmmPool, poolAddress, left, right);
    } catch (error: unknown) {
      lastError = error;
      if (attempt === maxAttempts) {
        break;
      }

      const waitSec = RETRY_BACKOFF_SEC[attempt - 1] ?? 120;
      console.warn(
        `Snapshot fetch failed (attempt ${attempt}/${maxAttempts}): ${formatFetchError(error)}`,
      );
      console.warn(`Retrying in ${waitSec}s...`);
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

export async function fetchSnapshotSeries(
  connection: Connection,
  poolAddress: string,
  options: FetchSnapshotSeriesOptions,
): Promise<SnapshotSeriesManifest> {
  const seriesStartedAtUtc = new Date().toISOString();
  const snapshots: SnapshotSeriesEntry[] = [];
  const cooldownSec = options.cooldownSec ?? 20;
  const { left, right } = options.bounded;

  const dlmmPool = await DLMM.create(connection, new PublicKey(poolAddress));

  for (let index = 0; index < options.count; index += 1) {
    const result = await fetchBoundedSnapshotWithRetry(dlmmPool, poolAddress, left, right);
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
      const waitSec = cooldownSec + options.intervalSec;
      console.log(`Waiting ${waitSec}s before next snapshot (cooldown ${cooldownSec}s + interval ${options.intervalSec}s)...`);
      await sleep(waitSec * 1000);
    }
  }

  return {
    pool_address: poolAddress,
    series_started_at_utc: seriesStartedAtUtc,
    series_completed_at_utc: new Date().toISOString(),
    interval_sec: options.intervalSec,
    cooldown_sec: cooldownSec,
    snapshot_count: options.count,
    bounded: options.bounded,
    snapshots,
  };
}
