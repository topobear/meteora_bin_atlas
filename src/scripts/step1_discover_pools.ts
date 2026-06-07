import path from "node:path";

import {
  discoverPools,
  formatTimestampForFilename,
  POOL_CANDIDATE_CSV_HEADERS,
  poolCandidatesToCsvRows,
} from "../meteora/discoverPools.js";
import { writeCsv } from "../io/writeCsv.js";
import { writeJson } from "../io/writeJson.js";
import { getConnection } from "../solana.js";

const PROJECT_ROOT = process.cwd();

async function main(): Promise<void> {
  const connection = getConnection();
  const timestamp = formatTimestampForFilename();
  const rawPath = path.join(PROJECT_ROOT, "data", "raw", `pools_${timestamp}.json`);
  const csvPath = path.join(PROJECT_ROOT, "data", "processed", "pool_candidates.csv");

  const { result, rawPayload } = await discoverPools(connection, PROJECT_ROOT);

  await writeJson(rawPath, rawPayload);
  await writeCsv(csvPath, [...POOL_CANDIDATE_CSV_HEADERS], poolCandidatesToCsvRows(result.candidates));

  console.log(`Discovery method: ${result.discovery_method}`);
  console.log(`Candidate pools: ${result.candidates.length}`);
  console.log(`Raw output: ${rawPath}`);
  console.log(`Processed output: ${csvPath}`);

  if (result.warnings.length > 0) {
    console.log("Warnings:");
    for (const warning of result.warnings) {
      console.log(`- ${warning}`);
    }
  }

  if (result.candidates.length === 0) {
    throw new Error("No candidate pools discovered.");
  }

  console.log("Pool discovery complete.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Pool discovery failed: ${message}`);
  process.exit(1);
});
