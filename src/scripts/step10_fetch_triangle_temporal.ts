import path from "node:path";

import {
  type DatasetId,
  getDatasetId,
  logRpcSource,
  resolveRpcDataset,
} from "../datasets.js";
import { formatTimestampForFilename } from "../meteora/discoverPools.js";
import {
  fetchTriangleSeries,
  type TriangleLegInput,
} from "../meteora/fetchTriangleSeries.js";
import { resolveSnapshotCacheConfig } from "../meteora/snapshotCache.js";
import { normalizeTriangleSeries } from "../meteora/normalizeTriangleSeries.js";
import {
  loadTrianglePreset,
  resolveTriangleLegs,
} from "../meteora/resolveTriangleLegs.js";
import { RpcDatasetAbortError } from "../meteora/fetchSnapshotSeries.js";
import { formatRpcDatasetWarning, isRpcDatasetError } from "../rpcErrors.js";
import { getConnection } from "../solana.js";

function getFlagValue(flag: string): string | undefined {
  const flagIndex = process.argv.indexOf(flag);
  return flagIndex !== -1 && process.argv[flagIndex + 1]
    ? process.argv[flagIndex + 1]
    : undefined;
}

function getPositiveFlagNumber(flag: string, defaultValue: number, label: string): number {
  const raw = getFlagValue(flag);
  const value = raw ? Number(raw) : defaultValue;
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${label} must be a positive number.`);
  }
  return Math.trunc(value);
}

function getOptionalNonNegativeFlagNumber(flag: string, label: string): number | undefined {
  const raw = getFlagValue(flag);
  if (raw === undefined) {
    return undefined;
  }

  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`${label} must be a non-negative number.`);
  }

  return value;
}

function getFlagNumber(flag: string, defaultValue: number, label: string): number {
  const raw = getFlagValue(flag);
  const value = raw ? Number(raw) : defaultValue;
  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`${label} must be a non-negative number.`);
  }
  return value;
}

function exitOnRpcDatasetError(error: unknown, dataset: DatasetId): never {
  const message =
    error instanceof RpcDatasetAbortError
      ? error.message
      : formatRpcDatasetWarning(error, dataset);

  console.warn(`WARNING: ${message}`);
  console.warn("Triangle temporal fetch aborted — no series CSVs written.");
  process.exit(1);
}

async function main(): Promise<void> {
  const dataset = getDatasetId();
  if (dataset === "simulated") {
    throw new Error(
      "simulated dataset skips RPC polling — use `make triangle-temporal DATASET=simulated` instead.",
    );
  }

  const rpcDataset = resolveRpcDataset(dataset);
  const triangleFlag = getFlagValue("--triangle") ?? "sol_usdc_weth";
  const presetPath = path.isAbsolute(triangleFlag)
    ? triangleFlag
    : path.join(process.cwd(), "data", "triangles", `${triangleFlag}.json`);
  const countPerLeg = getPositiveFlagNumber("--count", 240, "--count");
  const intervalSec =
    getOptionalNonNegativeFlagNumber("--interval-sec", "--interval-sec") ?? rpcDataset.intervalSec;
  const rpcBackoffSec =
    getOptionalNonNegativeFlagNumber("--rpc-backoff-sec", "--rpc-backoff-sec") ??
    rpcDataset.rpcBackoffSec;
  const binsLeft = getFlagNumber("--bins-left", 30, "--bins-left");
  const binsRight = getFlagNumber("--bins-right", 30, "--bins-right");

  const projectRoot = process.cwd();
  const preset = await loadTrianglePreset(presetPath);
  const resolved = await resolveTriangleLegs(preset, projectRoot);

  const missing = Object.entries(resolved).filter(([, leg]) => !leg.pool_address);
  if (missing.length > 0) {
    throw new Error(
      `Missing pools for legs: ${missing.map(([key]) => key).join(", ")}. ` +
        `Try fallback preset or add leg_pools overrides.`,
    );
  }

  const legKeys = [
    `${preset.tokens[0].symbol}-${preset.tokens[1].symbol}`,
    `${preset.tokens[1].symbol}-${preset.tokens[2].symbol}`,
    `${preset.tokens[2].symbol}-${preset.tokens[0].symbol}`,
  ];
  const legs: TriangleLegInput[] = legKeys.map((legKey, index) => {
    const leg = resolved[legKey];
    if (!leg?.pool_address) {
      throw new Error(`Missing pool for leg ${legKey}`);
    }
    return {
      leg_index: index,
      leg_key: legKey,
      pool_address: leg.pool_address,
    };
  });

  const totalFetches = countPerLeg * legs.length;
  const pauseSec = rpcBackoffSec + intervalSec;
  const wallMin = Math.round((pauseSec * Math.max(totalFetches - 1, 0)) / 60);
  const runTimestamp = formatTimestampForFilename();
  const connection = getConnection(rpcDataset.rpcUrl);

  console.log(`Triangle temporal fetch: ${preset.id}`);
  logRpcSource(dataset, rpcDataset.rpcUrl);
  for (const leg of legs) {
    console.log(`  ${leg.leg_key}: ${leg.pool_address}`);
  }
  console.log(
    `  ${countPerLeg} snapshots/leg (${totalFetches} interleaved fetches), ` +
      `${pauseSec}s between fetches (~${wallMin} min)`,
  );
  console.log("");

  let manifest;
  try {
    manifest = await fetchTriangleSeries(connection, {
      triangleId: preset.id,
      legs,
      countPerLeg,
      intervalSec,
      rpcBackoffSec,
      projectRoot,
      dataset,
      bounded: { left: binsLeft, right: binsRight },
      cache: resolveSnapshotCacheConfig(),
    });
  } catch (error: unknown) {
    if (error instanceof RpcDatasetAbortError || isRpcDatasetError(error)) {
      exitOnRpcDatasetError(error, dataset);
    }
    throw error;
  }

  const normalized = await normalizeTriangleSeries(manifest, projectRoot, runTimestamp);
  console.log(`Triangle manifest → ${normalized.manifestPath}`);
  for (const csvPath of normalized.legCsvPaths) {
    console.log(`  Leg CSV → ${csvPath}`);
  }
  console.log("Triangle temporal fetch complete.");
}

main().catch((error: unknown) => {
  const dataset = getDatasetId();
  if (error instanceof RpcDatasetAbortError || isRpcDatasetError(error)) {
    exitOnRpcDatasetError(error, dataset);
  }

  const message = error instanceof Error ? error.message : String(error);
  console.error(`Triangle temporal fetch failed: ${message}`);
  process.exit(1);
});
