import dotenv from "dotenv";

dotenv.config();

export const DATASET_IDS = ["alchemy", "solana-public", "simulated"] as const;
export type DatasetId = (typeof DATASET_IDS)[number];

export const DEFAULT_DATASET: DatasetId = "alchemy";

export const SOLANA_PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com";

/** Default temporal pacing: 2 Hz poll on Alchemy. */
export const DEFAULT_POLL_HZ = 2;

/** Empirical bounded-fetch RPC latency on Alchemy (~100ms). */
export const FETCH_LATENCY_SEC = 0.1;

export type RpcDatasetConfig = {
  id: DatasetId;
  rpcUrl: string;
  rpcBackoffSec: number;
  intervalSec: number;
};

export function pollIntervalSec(pollHz: number): number {
  if (pollHz <= 0) {
    throw new Error("pollHz must be positive");
  }
  return Math.max(0, 1 / pollHz - FETCH_LATENCY_SEC);
}

function parseDatasetFlag(argv: string[]): DatasetId | undefined {
  const flagIndex = argv.indexOf("--dataset");
  if (flagIndex === -1 || !argv[flagIndex + 1]) {
    return undefined;
  }

  const value = argv[flagIndex + 1];
  if (!DATASET_IDS.includes(value as DatasetId)) {
    throw new Error(`--dataset must be one of: ${DATASET_IDS.join(", ")}`);
  }

  return value as DatasetId;
}

export function getDatasetId(argv: string[] = process.argv): DatasetId {
  return parseDatasetFlag(argv) ?? DEFAULT_DATASET;
}

export function resolveRpcDataset(dataset: DatasetId): RpcDatasetConfig {
  if (dataset === "simulated") {
    throw new Error("simulated dataset does not use Solana RPC");
  }

  const intervalSec = pollIntervalSec(DEFAULT_POLL_HZ);

  if (dataset === "solana-public") {
    return {
      id: dataset,
      rpcUrl: SOLANA_PUBLIC_RPC_URL,
      rpcBackoffSec: 5,
      intervalSec: Math.max(intervalSec, 0.9),
    };
  }

  const rpcUrl = process.env.SOLANA_RPC_URL?.trim();
  if (!rpcUrl) {
    throw new Error(
      "alchemy dataset requires SOLANA_RPC_URL in .env (e.g. Alchemy mainnet endpoint).",
    );
  }

  return {
    id: dataset,
    rpcUrl,
    rpcBackoffSec: 0,
    intervalSec,
  };
}
