import { config } from "../config.js";
import { logRpcSourceFromUrl } from "../datasets.js";
import { getConnection } from "../solana.js";

async function main(): Promise<void> {
  logRpcSourceFromUrl(config.SOLANA_RPC_URL);
  const connection = getConnection();
  const slot = await connection.getSlot();
  console.log(`Cluster: ${config.SOLANA_CLUSTER}`);
  console.log(`Current slot: ${slot}`);
  console.log("Smoke test passed.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Smoke test failed: ${message}`);
  process.exit(1);
});
