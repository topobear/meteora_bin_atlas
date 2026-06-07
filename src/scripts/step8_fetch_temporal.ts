import path from "node:path";

import { formatTimestampForFilename } from "../meteora/discoverPools.js";
import { fetchPoolOhlcv, type OhlcvTimeframe } from "../meteora/fetchPoolOhlcv.js";
import { fetchSnapshotSeries } from "../meteora/fetchSnapshotSeries.js";
import {
  BIN_ATLAS_SERIES_CSV_HEADERS,
  normalizeSnapshotSeries,
} from "../meteora/normalizeSnapshotSeries.js";
import { writeCsv } from "../io/writeCsv.js";
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

function getFlagNumber(flag: string, defaultValue: number, label: string): number {
  const flagIndex = process.argv.indexOf(flag);
  const value =
    flagIndex !== -1 && process.argv[flagIndex + 1]
      ? Number(process.argv[flagIndex + 1])
      : defaultValue;

  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`${label} must be a non-negative number.`);
  }

  return value;
}

function getPositiveFlagNumber(flag: string, defaultValue: number, label: string): number {
  const value = getFlagNumber(flag, defaultValue, label);
  if (value <= 0) {
    throw new Error(`${label} must be a positive number.`);
  }
  return Math.trunc(value);
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

function getRpcBackoffSec(): number {
  const rpcFlagIndex = process.argv.indexOf("--rpc-backoff-sec");
  const cooldownFlagIndex = process.argv.indexOf("--cooldown-sec");
  const flagIndex = rpcFlagIndex !== -1 ? rpcFlagIndex : cooldownFlagIndex;
  const value =
    flagIndex !== -1 && process.argv[flagIndex + 1] ? Number(process.argv[flagIndex + 1]) : 60;

  if (!Number.isFinite(value) || value < 0) {
    throw new Error("--rpc-backoff-sec must be a non-negative number.");
  }

  return value;
}

async function main(): Promise<void> {
  const poolAddress = getPoolAddress();
  const timeframe = getTimeframe();
  const lookbackDays = getPositiveFlagNumber("--lookback-days", 7, "--lookback-days");
  const count = getPositiveFlagNumber("--count", 10, "--count");
  const intervalSec = getFlagNumber("--interval-sec", 30, "--interval-sec");
  const rpcBackoffSec = getRpcBackoffSec();
  const binsLeft = getFlagNumber("--bins-left", 30, "--bins-left");
  const binsRight = getFlagNumber("--bins-right", 30, "--bins-right");

  const pauseSec = rpcBackoffSec + intervalSec;
  const wallMin = Math.round((pauseSec * Math.max(count - 1, 0)) / 60);
  const projectRoot = process.cwd();
  const connection = getConnection();
  const runTimestamp = formatTimestampForFilename();

  console.log(`Temporal sample for pool ${poolAddress}`);
  console.log(`  OHLCV: ${lookbackDays}d ${timeframe} candles (Meteora datapi)`);
  console.log(
    `  Series: ${count} bounded snapshots (${binsLeft}/${binsRight} bins), ` +
      `${pauseSec}s between snapshots (RPC backoff ${rpcBackoffSec}s, then interval ${intervalSec}s, ~${wallMin} min)`,
  );
  console.log("");

  const ohlcv = await fetchPoolOhlcv(poolAddress, { timeframe, lookbackDays });
  const ohlcvStem = `pool_ohlcv_${poolAddress}_${timeframe}_${runTimestamp}`;
  const ohlcvPath = path.join(projectRoot, "data", "processed", `${ohlcvStem}.json`);
  await writeJson(ohlcvPath, ohlcv);
  console.log(`OHLCV: ${ohlcv.data.length} candles → ${ohlcvPath}`);
  if (ohlcv.data.length > 0) {
    console.log(`  Range: ${ohlcv.data[0].timestamp_str} → ${ohlcv.data.at(-1)?.timestamp_str}`);
  }
  console.log("");

  const manifest = await fetchSnapshotSeries(connection, poolAddress, {
    count,
    intervalSec,
    rpcBackoffSec,
    projectRoot,
    bounded: { left: binsLeft, right: binsRight },
  });

  const seriesStem = `snapshot_series_${poolAddress}_${runTimestamp}`;
  const manifestPath = path.join(projectRoot, "data", "processed", `${seriesStem}.json`);
  await writeJson(manifestPath, manifest);
  console.log(`Series manifest: ${manifest.snapshots.length} snapshots → ${manifestPath}`);
  console.log("");

  const { rows, result } = await normalizeSnapshotSeries(manifest, projectRoot);
  const seriesCsvPath = path.join(projectRoot, "data", "processed", `${result.outputStem}.csv`);
  await writeCsv(seriesCsvPath, [...BIN_ATLAS_SERIES_CSV_HEADERS], rows);

  console.log(`Series CSV: ${result.rowCount} rows → ${seriesCsvPath}`);
  console.log("Temporal fetch complete.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Temporal fetch failed: ${message}`);
  process.exit(1);
});
