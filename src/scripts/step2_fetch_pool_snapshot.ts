import path from "node:path";

import { formatTimestampForFilename } from "../meteora/discoverPools.js";
import { fetchPoolState } from "../meteora/fetchPoolState.js";
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

async function main(): Promise<void> {
  const poolAddress = getPoolAddress();
  const connection = getConnection();
  const timestamp = formatTimestampForFilename();
  const fileStem = `pool_snapshot_${poolAddress}_${timestamp}`;
  const rawPath = path.join(process.cwd(), "data", "raw", `${fileStem}.json`);
  const processedPath = path.join(process.cwd(), "data", "processed", `${fileStem}.json`);

  const { processed, raw } = await fetchPoolState(connection, poolAddress);

  await writeJson(rawPath, raw);
  await writeJson(processedPath, processed);

  console.log(`Pool: ${processed.pool_address}`);
  console.log(`Active bin ID: ${processed.active_bin_id}`);
  console.log(`Active bin price: ${processed.active_bin_price}`);
  console.log(`Bin step: ${processed.bin_step} bps`);
  console.log(`Token X mint: ${processed.token_x_mint}`);
  console.log(`Token Y mint: ${processed.token_y_mint}`);
  console.log(`Raw output: ${rawPath}`);
  console.log(`Processed output: ${processedPath}`);
  console.log("Pool snapshot complete.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Pool snapshot failed: ${message}`);
  process.exit(1);
});
