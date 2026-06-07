import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

import { formatTimestampForFilename } from "../meteora/discoverPools.js";
import {
  BIN_ATLAS_CSV_HEADERS,
  binAtlasRowsToCsvRows,
  normalizeBinArrays,
  type RawBinArraysFile,
} from "../meteora/normalizeBins.js";
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

async function findLatestBinArraysFile(poolAddress: string): Promise<string> {
  const rawDir = path.join(process.cwd(), "data", "raw");
  const entries = await readdir(rawDir);
  const prefix = `bin_arrays_${poolAddress}_`;

  const matches = entries
    .filter((entry) => entry.startsWith(prefix) && entry.endsWith(".json"))
    .sort()
    .reverse();

  if (matches.length === 0) {
    throw new Error(
      `No raw bin arrays file found for pool ${poolAddress} in ${rawDir}. Run npm run fetch:bins first.`,
    );
  }

  return path.join(rawDir, matches[0]);
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

  return findLatestBinArraysFile(poolAddress);
}

async function main(): Promise<void> {
  const inputPath = await resolveInputPath();
  const contents = await readFile(inputPath, "utf8");
  const file = JSON.parse(contents) as RawBinArraysFile;

  const rows = normalizeBinArrays(file);
  if (rows.length === 0) {
    throw new Error("Normalization produced zero bin rows.");
  }

  const timestamp = formatTimestampForFilename(new Date(file.fetched_at_utc));
  const outputStem = `bin_atlas_${file.pool_address}_${timestamp}`;
  const csvPath = path.join(process.cwd(), "data", "processed", `${outputStem}.csv`);

  await writeCsv(csvPath, [...BIN_ATLAS_CSV_HEADERS], binAtlasRowsToCsvRows(rows));

  const activeRows = rows.filter((row) => row.is_active_bin);
  const nonZeroLiquidity = rows.filter((row) => row.liquidity !== "0" && row.liquidity !== "");

  console.log(`Input: ${inputPath}`);
  console.log(`Method: ${file.method}`);
  console.log(`Bins normalized: ${rows.length}`);
  console.log(`Active bin rows: ${activeRows.length}`);
  console.log(`Bins with non-zero liquidity: ${nonZeroLiquidity.length}`);
  if (activeRows[0]) {
    console.log(`Active bin ID: ${activeRows[0].bin_id}`);
  }
  console.log(`Processed output: ${csvPath}`);
  console.log("Bin atlas normalization complete.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Bin atlas normalization failed: ${message}`);
  process.exit(1);
});
