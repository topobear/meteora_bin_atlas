import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

import {
  BIN_ATLAS_SERIES_CSV_HEADERS,
  normalizeSnapshotSeries,
} from "../meteora/normalizeSnapshotSeries.js";
import type { SnapshotSeriesManifest } from "../meteora/fetchSnapshotSeries.js";
import { writeCsv } from "../io/writeCsv.js";

function getPoolAddress(): string | undefined {
  const poolFlagIndex = process.argv.indexOf("--pool");
  if (poolFlagIndex !== -1 && process.argv[poolFlagIndex + 1]) {
    return process.argv[poolFlagIndex + 1];
  }

  return process.env.METEORA_POOL_ADDRESS;
}

function getInputPath(): string | undefined {
  const inputFlagIndex = process.argv.indexOf("--input");
  if (inputFlagIndex !== -1 && process.argv[inputFlagIndex + 1]) {
    return process.argv[inputFlagIndex + 1];
  }

  return undefined;
}

async function findLatestSnapshotSeriesFile(poolAddress: string): Promise<string> {
  const processedDir = path.join(process.cwd(), "data", "processed");
  const entries = await readdir(processedDir);
  const prefix = `snapshot_series_${poolAddress}_`;

  const matches = entries
    .filter((entry) => entry.startsWith(prefix) && entry.endsWith(".json"))
    .sort()
    .reverse();

  if (matches.length === 0) {
    throw new Error(
      `No snapshot series manifest found for pool ${poolAddress} in ${processedDir}. Run npm run fetch:series first.`,
    );
  }

  return path.join(processedDir, matches[0]);
}

async function resolveInputPath(): Promise<string> {
  const explicitInput = getInputPath();
  if (explicitInput) {
    return path.isAbsolute(explicitInput)
      ? explicitInput
      : path.join(process.cwd(), explicitInput);
  }

  const poolAddress = getPoolAddress();
  if (!poolAddress) {
    throw new Error(
      "Provide --input <path> or --pool <POOL_ADDRESS> (or set METEORA_POOL_ADDRESS).",
    );
  }

  return findLatestSnapshotSeriesFile(poolAddress);
}

async function main(): Promise<void> {
  const inputPath = await resolveInputPath();
  const contents = await readFile(inputPath, "utf8");
  const manifest = JSON.parse(contents) as SnapshotSeriesManifest;

  const { rows, result } = await normalizeSnapshotSeries(manifest, process.cwd());
  if (rows.length === 0) {
    throw new Error("Normalization produced zero bin rows.");
  }

  const csvPath = path.join(
    process.cwd(),
    "data",
    "processed",
    `${result.outputStem}.csv`,
  );

  await writeCsv(csvPath, [...BIN_ATLAS_SERIES_CSV_HEADERS], rows);

  console.log(`Input: ${inputPath}`);
  console.log(`Snapshots normalized: ${result.snapshotCount}`);
  console.log(`Rows written: ${result.rowCount}`);
  console.log(`Processed output: ${csvPath}`);
  console.log("Snapshot series normalization complete.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Snapshot series normalization failed: ${message}`);
  process.exit(1);
});
