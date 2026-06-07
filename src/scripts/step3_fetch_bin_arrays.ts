import path from "node:path";

import { formatTimestampForFilename } from "../meteora/discoverPools.js";
import { fetchBinArrays } from "../meteora/fetchBinArrays.js";
import { writeJson } from "../io/writeJson.js";
import { getConnection } from "../solana.js";

function getPoolAddress(): string {
  const poolFlagIndex = process.argv.indexOf("--pool");
  const cliPool =
    poolFlagIndex !== -1 && process.argv[poolFlagIndex + 1]
      ? process.argv[poolFlagIndex + 1]
      : undefined;
  const poolAddress = cliPool ?? process.env.METEORA_POOL_ADDRESS;

  if (!poolAddress) {
    throw new Error(
      "Pool address required. Pass --pool <POOL_ADDRESS> or set METEORA_POOL_ADDRESS.",
    );
  }

  return poolAddress;
}

function getBoundedOptions(): { left: number; right: number } | undefined {
  if (!process.argv.includes("--bounded")) {
    return undefined;
  }

  const leftFlagIndex = process.argv.indexOf("--bins-left");
  const rightFlagIndex = process.argv.indexOf("--bins-right");

  const left =
    leftFlagIndex !== -1 && process.argv[leftFlagIndex + 1]
      ? Number(process.argv[leftFlagIndex + 1])
      : 20;
  const right =
    rightFlagIndex !== -1 && process.argv[rightFlagIndex + 1]
      ? Number(process.argv[rightFlagIndex + 1])
      : 20;

  if (!Number.isFinite(left) || !Number.isFinite(right) || left < 0 || right < 0) {
    throw new Error("--bins-left and --bins-right must be non-negative numbers.");
  }

  return { left, right };
}

async function main(): Promise<void> {
  const poolAddress = getPoolAddress();
  const bounded = getBoundedOptions();
  const connection = getConnection();
  const timestamp = formatTimestampForFilename();
  const fileStem = `bin_arrays_${poolAddress}_${timestamp}`;
  const rawPath = path.join(process.cwd(), "data", "raw", `${fileStem}.json`);

  const result = await fetchBinArrays(connection, poolAddress, bounded ? { bounded } : undefined);

  await writeJson(rawPath, {
    pool_address: result.pool_address,
    fetched_at_utc: result.fetched_at_utc,
    method: result.method,
    summary: result.summary,
    raw: result.raw,
  });

  console.log(`Pool: ${result.pool_address}`);
  console.log(`Method: ${result.method}`);
  if (result.summary?.bin_array_count !== undefined) {
    console.log(`Bin arrays fetched: ${result.summary.bin_array_count}`);
  }
  if (result.summary?.bin_count !== undefined) {
    console.log(`Bins fetched: ${result.summary.bin_count}`);
  }
  if (result.summary?.active_bin_id !== undefined) {
    console.log(`Active bin ID: ${result.summary.active_bin_id}`);
  }
  console.log(`Raw output: ${rawPath}`);
  console.log("Bin arrays fetch complete.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Bin arrays fetch failed: ${message}`);
  process.exit(1);
});
