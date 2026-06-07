import { Connection } from "@solana/web3.js";

import { config } from "./config.js";

export function getConnection(): Connection {
  return new Connection(config.SOLANA_RPC_URL, "confirmed");
}
