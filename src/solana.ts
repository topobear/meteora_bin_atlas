import { Connection } from "@solana/web3.js";

import { config } from "./config.js";

export function getConnection(rpcUrl: string = config.SOLANA_RPC_URL): Connection {
  return new Connection(rpcUrl, "confirmed");
}
