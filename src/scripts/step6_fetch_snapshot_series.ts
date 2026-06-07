import path from "node:path";

import { formatTimestampForFilename } from "../meteora/discoverPools.js";
import { fetchSnapshotSeries } from "../meteora/fetchSnapshotSeries.js";
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

function getCount(): number {
  const flagIndex = process.argv.indexOf("--count");
  const value = flagIndex !== -1 && process.argv[flagIndex + 1] ? Number(process.argv[flagIndex + 1]) : 20;

  if (!Number.isFinite(value) || value < 1) {
    throw new Error("--count must be a positive integer.");
  }

  return Math.trunc(value);
}

function getIntervalSec(): number {
  const flagIndex = process.argv.indexOf("--interval-sec");
  const value =
    flagIndex !== -1 && process.argv[flagIndex + 1] ? Number(process.argv[flagIndex + 1]) : 90;

  if (!Number.isFinite(value) || value < 0) {
    throw new Error("--interval-sec must be a non-negative number.");
  }

  return value;
}

function getCooldownSec(): number {
  const flagIndex = process.argv.indexOf("--cooldown-sec");
  const value =
    flagIndex !== -1 && process.argv[flagIndex + 1] ? Number(process.argv[flagIndex + 1]) : 20;

  if (!Number.isFinite(value) || value < 0) {
    throw new Error("--cooldown-sec must be a non-negative number.");
  }

  return value;
}

function getBoundedOptions(): { left: number; right: number } {
  const leftFlagIndex = process.argv.indexOf("--bins-left");
  const rightFlagIndex = process.argv.indexOf("--bins-right");

  const left =
    leftFlagIndex !== -1 && process.argv[leftFlagIndex + 1]
      ? Number(process.argv[leftFlagIndex + 1])
      : 30;
  const right =
    rightFlagIndex !== -1 && process.argv[rightFlagIndex + 1]
      ? Number(process.argv[rightFlagIndex + 1])
      : 30;

  if (!Number.isFinite(left) || !Number.isFinite(right) || left < 0 || right < 0) {
    throw new Error("--bins-left and --bins-right must be non-negative numbers.");
  }

  return { left, right };
}

async function main(): Promise<void> {
  const poolAddress = getPoolAddress();
  const count = getCount();
  const intervalSec = getIntervalSec();
  const cooldownSec = getCooldownSec();
  const bounded = getBoundedOptions();
  const connection = getConnection();
  const timestamp = formatTimestampForFilename();
  const fileStem = `snapshot_series_${poolAddress}_${timestamp}`;
  const rawPath = path.join(process.cwd(), "data", "raw", `${fileStem}.json`);
  const processedPath = path.join(process.cwd(), "data", "processed", `${fileStem}.json`);

  const pauseSec = cooldownSec + intervalSec;
  const durationSec = pauseSec * Math.max(count - 1, 0);
  console.log(
    `Collecting ${count} bounded snapshots (${bounded.left}/${bounded.right} bins). ` +
      `Pause ${pauseSec}s between snapshots (cooldown ${cooldownSec}s + interval ${intervalSec}s) ` +
      `(~${Math.round(durationSec / 60)} min wall time).`,
  );

  const manifest = await fetchSnapshotSeries(connection, poolAddress, {
    count,
    intervalSec,
    cooldownSec,
    projectRoot: process.cwd(),
    bounded,
  });

  await writeJson(rawPath, manifest);
  await writeJson(processedPath, manifest);

  console.log(`Pool: ${manifest.pool_address}`);
  console.log(`Snapshots: ${manifest.snapshot_count}`);
  console.log(`Interval: ${manifest.interval_sec}s | Cooldown: ${manifest.cooldown_sec}s`);
  console.log(`Raw output: ${rawPath}`);
  console.log(`Processed output: ${processedPath}`);
  console.log("Snapshot series fetch complete.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Snapshot series fetch failed: ${message}`);
  process.exit(1);
});
