import { config } from "../config.js";
import { getConnection } from "../solana.js";

async function main(): Promise<void> {
  const connection = getConnection();
  const slot = await connection.getSlot();
  const rpcHost = new URL(config.SOLANA_RPC_URL).host;

  console.log(`RPC host: ${rpcHost}`);
  console.log(`Cluster: ${config.SOLANA_CLUSTER}`);
  console.log(`Current slot: ${slot}`);
  console.log("Smoke test passed.");
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Smoke test failed: ${message}`);
  process.exit(1);
});
