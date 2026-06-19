import { readdir, readFile, unlink } from "node:fs/promises";
import path from "node:path";

import type { SnapshotSeriesEntry } from "./fetchSnapshotSeries.js";

/** Default TTL for bounded snapshot reuse (1 hour). */
export const DEFAULT_SNAPSHOT_CACHE_TTL_SEC = 3600;

export type SnapshotCacheConfig = {
  enabled: boolean;
  ttlSec: number;
};

type RawSnapshotMeta = {
  rawFilename: string;
  poolAddress: string;
  fetchedAtUtc: string;
  fetchedAtMs: number;
  binsLeft?: number;
  binsRight?: number;
  activeBinId?: number;
  binCount?: number;
};

function rawDir(projectRoot: string): string {
  return path.join(projectRoot, "data", "raw");
}

function poolRawPrefix(poolAddress: string): string {
  return `bin_arrays_${poolAddress}_`;
}

export function resolveSnapshotCacheConfig(argv: string[] = process.argv): SnapshotCacheConfig {
  const noCache = argv.includes("--no-snapshot-cache");
  const ttlFlagIndex = argv.indexOf("--snapshot-cache-ttl-sec");
  const envTtl = process.env.SNAPSHOT_CACHE_TTL_SEC;

  let ttlSec = DEFAULT_SNAPSHOT_CACHE_TTL_SEC;
  if (ttlFlagIndex !== -1 && argv[ttlFlagIndex + 1]) {
    ttlSec = Number(argv[ttlFlagIndex + 1]);
  } else if (envTtl) {
    ttlSec = Number(envTtl);
  }

  if (!Number.isFinite(ttlSec) || ttlSec < 0) {
    throw new Error("snapshot cache TTL must be a non-negative number of seconds.");
  }

  return {
    enabled: !noCache && ttlSec > 0,
    ttlSec,
  };
}

async function readRawMeta(rawFilename: string, rawPath: string): Promise<RawSnapshotMeta | null> {
  try {
    const contents = await readFile(rawPath, "utf8");
    const file = JSON.parse(contents) as {
      pool_address?: string;
      fetched_at_utc?: string;
      method?: string;
      summary?: {
        bins_left?: number;
        bins_right?: number;
        active_bin_id?: number;
        bin_count?: number;
      };
    };

    if (
      !file.pool_address ||
      !file.fetched_at_utc ||
      file.method !== "getBinsAroundActiveBin"
    ) {
      return null;
    }

    const fetchedAtMs = Date.parse(file.fetched_at_utc);
    if (!Number.isFinite(fetchedAtMs)) {
      return null;
    }

    return {
      rawFilename,
      poolAddress: file.pool_address,
      fetchedAtUtc: file.fetched_at_utc,
      fetchedAtMs,
      binsLeft: file.summary?.bins_left,
      binsRight: file.summary?.bins_right,
      activeBinId: file.summary?.active_bin_id,
      binCount: file.summary?.bin_count,
    };
  } catch {
    return null;
  }
}

function matchesBounds(meta: RawSnapshotMeta, left: number, right: number): boolean {
  return meta.binsLeft === left && meta.binsRight === right;
}

/** Delete bounded snapshot raw files older than the TTL window. */
export async function pruneExpiredSnapshotCache(
  projectRoot: string,
  ttlSec: number,
  nowMs = Date.now(),
): Promise<number> {
  if (ttlSec <= 0) {
    return 0;
  }

  const dir = rawDir(projectRoot);
  let entries: string[];
  try {
    entries = await readdir(dir);
  } catch {
    return 0;
  }

  const cutoffMs = nowMs - ttlSec * 1000;
  let deleted = 0;

  for (const name of entries) {
    if (!name.startsWith("bin_arrays_") || !name.endsWith(".json")) {
      continue;
    }

    const filePath = path.join(dir, name);
    const meta = await readRawMeta(name, filePath);
    if (!meta || meta.fetchedAtMs >= cutoffMs) {
      continue;
    }

    try {
      await unlink(filePath);
      deleted += 1;
    } catch (error: unknown) {
      const code = (error as NodeJS.ErrnoException | undefined)?.code;
      if (code !== "ENOENT") {
        throw error;
      }
    }
  }

  return deleted;
}

export async function listPoolCachedSnapshots(
  projectRoot: string,
  poolAddress: string,
  bounded: { left: number; right: number },
  ttlSec: number,
  nowMs = Date.now(),
): Promise<RawSnapshotMeta[]> {
  const dir = rawDir(projectRoot);
  const prefix = poolRawPrefix(poolAddress);
  const cutoffMs = nowMs - ttlSec * 1000;

  let entries: string[];
  try {
    entries = await readdir(dir);
  } catch {
    return [];
  }

  const matches: RawSnapshotMeta[] = [];

  for (const name of entries) {
    if (!name.startsWith(prefix) || !name.endsWith(".json")) {
      continue;
    }

    const meta = await readRawMeta(name, path.join(dir, name));
    if (!meta || meta.fetchedAtMs < cutoffMs || !matchesBounds(meta, bounded.left, bounded.right)) {
      continue;
    }

    matches.push(meta);
  }

  matches.sort((a, b) => a.fetchedAtMs - b.fetchedAtMs);
  return matches;
}

function toSeriesEntries(cached: RawSnapshotMeta[]): SnapshotSeriesEntry[] {
  return cached.map((snap, index) => ({
    index,
    fetched_at_utc: snap.fetchedAtUtc,
    raw_filename: snap.rawFilename,
    active_bin_id: snap.activeBinId,
    bin_count: snap.binCount,
  }));
}

/** Reuse the most recent `count` bounded snapshots when enough exist within TTL. */
export async function tryResolveSeriesFromCache(
  projectRoot: string,
  poolAddress: string,
  count: number,
  bounded: { left: number; right: number },
  config: SnapshotCacheConfig,
): Promise<SnapshotSeriesEntry[] | null> {
  if (!config.enabled) {
    return null;
  }

  await pruneExpiredSnapshotCache(projectRoot, config.ttlSec);

  const cached = await listPoolCachedSnapshots(
    projectRoot,
    poolAddress,
    bounded,
    config.ttlSec,
  );
  if (cached.length < count) {
    return null;
  }

  return toSeriesEntries(cached.slice(-count));
}
