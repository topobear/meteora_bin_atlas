import path from "node:path";

import { formatTimestampForFilename } from "../meteora/discoverPools.js";
import { fetchPoolOhlcv, type OhlcvTimeframe } from "../meteora/fetchPoolOhlcv.js";
import { writeJson } from "../io/writeJson.js";

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

function getTimeframe(): OhlcvTimeframe {
  const flagIndex = process.argv.indexOf("--timeframe");
  const value =
    flagIndex !== -1 && process.argv[flagIndex + 1] ? process.argv[flagIndex + 1] : "1h";

  const allowed: OhlcvTimeframe[] = ["5m", "30m", "1h", "2h", "4h", "12h", "24h"];
  if (!allowed.includes(value as OhlcvTimeframe)) {
    throw new Error(`--timeframe must be one of: ${allowed.join(", ")}`);
  }

  return value as OhlcvTimeframe;
}

function getLookbackDays(): number {
  const flagIndex = process.argv.indexOf("--lookback-days");
  const value =
    flagIndex !== -1 && process.argv[flagIndex + 1] ? Number(process.argv[flagIndex + 1]) : 7;

  if (!Number.isFinite(value) || value <= 0) {
    throw new Error("--lookback-days must be a positive number.");
  }

  return value;
}

async function main(): Promise<void> {
  const poolAddress = getPoolAddress();
  const timeframe = getTimeframe();
  const lookbackDays = getLookbackDays();
  const timestamp = formatTimestampForFilename();
  const fileStem = `pool_ohlcv_${poolAddress}_${timeframe}_${timestamp}`;
  const rawPath = path.join(process.cwd(), "data", "raw", `${fileStem}.json`);
  const processedPath = path.join(process.cwd(), "data", "processed", `${fileStem}.json`);

  const result = await fetchPoolOhlcv(poolAddress, { timeframe, lookbackDays });

  await writeJson(rawPath, result);
  await writeJson(processedPath, result);

  console.log(`Pool: ${result.pool_address}`);
  console.log(`Timeframe: ${result.timeframe}`);
  console.log(`Candles: ${result.data.length}`);
  console.log(`Chunks fetched: ${result.chunk_count}`);
  if (result.data.length > 0) {
    console.log(`Range: ${result.data[0].timestamp_str} → ${result.data.at(-1)?.timestamp_str}`);
  }
  console.log(`Raw output: ${rawPath}`);
  console.log(`Processed output: ${processedPath}`);
  console.log("Pool OHLCV fetch complete.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Pool OHLCV fetch failed: ${message}`);
  process.exit(1);
});
