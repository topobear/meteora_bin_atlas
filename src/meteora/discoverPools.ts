import { readFile } from "node:fs/promises";
import path from "node:path";

import DLMM from "@meteora-ag/dlmm";
import { Connection, PublicKey } from "@solana/web3.js";

import type {
  DiscoverySource,
  ManualPoolsFile,
  PoolCandidate,
  PoolDiscoveryResult,
} from "./types.js";

const METEORA_DATAPI_BASE = "https://dlmm.datapi.meteora.ag";
const LEGACY_METEORA_API_URL = "https://dlmm-api.meteora.ag/pair/all";
const DEFAULT_CANDIDATE_LIMIT = 10;

type MeteoraApiPool = {
  address: string;
  name?: string;
  token_x?: { address?: string };
  token_y?: { address?: string };
  pool_config?: { bin_step?: number };
};

type MeteoraApiPoolsResponse = {
  data?: MeteoraApiPool[];
};

export function formatTimestampForFilename(date = new Date()): string {
  return date.toISOString().replace(/[:.]/g, "-");
}

function emptyCandidateFields(
  poolAddress: string,
  source: DiscoverySource,
  fetchedAtUtc: string,
): PoolCandidate {
  return {
    pool_address: poolAddress,
    token_x_mint: "",
    token_y_mint: "",
    bin_step: "",
    active_bin_id: "",
    raw_name_or_symbol_if_available: "",
    source,
    fetched_at_utc: fetchedAtUtc,
  };
}

function normalizeApiPool(pool: MeteoraApiPool, fetchedAtUtc: string): PoolCandidate {
  return {
    pool_address: pool.address,
    token_x_mint: pool.token_x?.address ?? "",
    token_y_mint: pool.token_y?.address ?? "",
    bin_step: pool.pool_config?.bin_step ?? "",
    active_bin_id: "",
    raw_name_or_symbol_if_available: pool.name ?? "",
    source: "meteora_api",
    fetched_at_utc: fetchedAtUtc,
  };
}

async function fetchMeteoraApiPools(limit: number): Promise<{
  raw: MeteoraApiPoolsResponse;
  candidates: PoolCandidate[];
  warnings: string[];
}> {
  const fetchedAtUtc = new Date().toISOString();
  const warnings: string[] = [];

  const legacyResponse = await fetch(LEGACY_METEORA_API_URL);
  if (!legacyResponse.ok) {
    warnings.push(
      `Legacy Meteora API ${LEGACY_METEORA_API_URL} returned HTTP ${legacyResponse.status}; using ${METEORA_DATAPI_BASE}/pools instead.`,
    );
  }

  const url = new URL("/pools", METEORA_DATAPI_BASE);
  url.searchParams.set("page_size", String(limit));
  url.searchParams.set("sort_key", "tvl");
  url.searchParams.set("order_by", "desc");

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Meteora datapi /pools returned HTTP ${response.status}`);
  }

  const raw = (await response.json()) as MeteoraApiPoolsResponse;
  const pools = raw.data ?? [];

  if (pools.length === 0) {
    throw new Error("Meteora datapi /pools returned no pools");
  }

  return {
    raw,
    candidates: pools.map((pool) => normalizeApiPool(pool, fetchedAtUtc)),
    warnings,
  };
}

async function fetchMeteoraSdkPools(connection: Connection): Promise<{
  raw: Array<Record<string, unknown>>;
  candidates: PoolCandidate[];
}> {
  const fetchedAtUtc = new Date().toISOString();
  const pairs = await DLMM.getLbPairs(connection);

  const raw = pairs.map((pair) => ({
    publicKey: pair.publicKey.toBase58(),
    activeId: pair.account.activeId,
    binStep: pair.account.binStep,
    tokenXMint: pair.account.tokenXMint.toBase58(),
    tokenYMint: pair.account.tokenYMint.toBase58(),
  }));

  const candidates = pairs.map((pair) => ({
    pool_address: pair.publicKey.toBase58(),
    token_x_mint: pair.account.tokenXMint.toBase58(),
    token_y_mint: pair.account.tokenYMint.toBase58(),
    bin_step: pair.account.binStep,
    active_bin_id: pair.account.activeId,
    raw_name_or_symbol_if_available: "",
    source: "meteora_sdk" as const,
    fetched_at_utc: fetchedAtUtc,
  }));

  return { raw, candidates };
}

async function loadManualPools(projectRoot: string): Promise<{
  raw: ManualPoolsFile;
  candidates: PoolCandidate[];
}> {
  const fetchedAtUtc = new Date().toISOString();
  const manualPath = path.join(projectRoot, "data", "manual_pools.json");
  const contents = await readFile(manualPath, "utf8");
  const raw = JSON.parse(contents) as ManualPoolsFile;

  const candidates = raw.pools.map((pool) => ({
    ...emptyCandidateFields(pool.pool_address, "manual", fetchedAtUtc),
    raw_name_or_symbol_if_available: pool.note ?? "",
  }));

  return { raw, candidates };
}

export async function enrichCandidatesFromChain(
  connection: Connection,
  candidates: PoolCandidate[],
): Promise<{ candidates: PoolCandidate[]; warnings: string[] }> {
  const warnings: string[] = [];
  const enriched: PoolCandidate[] = [];

  for (const candidate of candidates) {
    if (candidate.active_bin_id !== "" && candidate.bin_step !== "" && candidate.token_x_mint) {
      enriched.push(candidate);
      continue;
    }

    try {
      const pool = await DLMM.create(connection, new PublicKey(candidate.pool_address));
      enriched.push({
        ...candidate,
        token_x_mint: candidate.token_x_mint || pool.lbPair.tokenXMint.toBase58(),
        token_y_mint: candidate.token_y_mint || pool.lbPair.tokenYMint.toBase58(),
        bin_step: candidate.bin_step === "" ? pool.lbPair.binStep : candidate.bin_step,
        active_bin_id:
          candidate.active_bin_id === "" ? pool.lbPair.activeId : candidate.active_bin_id,
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      warnings.push(`Could not enrich ${candidate.pool_address} from chain: ${message}`);
      enriched.push(candidate);
    }
  }

  return { candidates: enriched, warnings };
}

export async function discoverPools(
  connection: Connection,
  projectRoot: string,
  options?: { limit?: number },
): Promise<{
  result: PoolDiscoveryResult;
  rawPayload: unknown;
}> {
  const limit = options?.limit ?? DEFAULT_CANDIDATE_LIMIT;
  const warnings: string[] = [];

  try {
    const apiDiscovery = await fetchMeteoraApiPools(limit);
    warnings.push(...apiDiscovery.warnings);

    const enrichment = await enrichCandidatesFromChain(connection, apiDiscovery.candidates);
    warnings.push(...enrichment.warnings);

    return {
      rawPayload: {
        discovery_method: "meteora_api",
        endpoint: `${METEORA_DATAPI_BASE}/pools`,
        legacy_endpoint_checked: LEGACY_METEORA_API_URL,
        warnings,
        response: apiDiscovery.raw,
      },
      result: {
        discovery_method: "meteora_api",
        fetched_at_utc: new Date().toISOString(),
        warnings,
        candidates: enrichment.candidates.slice(0, limit),
      },
    };
  } catch (apiError: unknown) {
    const apiMessage = apiError instanceof Error ? apiError.message : String(apiError);
    warnings.push(`Meteora API discovery failed: ${apiMessage}`);
  }

  try {
    const sdkDiscovery = await fetchMeteoraSdkPools(connection);
    return {
      rawPayload: {
        discovery_method: "meteora_sdk",
        warnings,
        pairs: sdkDiscovery.raw,
      },
      result: {
        discovery_method: "meteora_sdk",
        fetched_at_utc: new Date().toISOString(),
        warnings: [
          ...warnings,
          "SDK discovery via DLMM.getLbPairs requires getProgramAccounts and may fail on rate-limited RPC endpoints.",
        ],
        candidates: sdkDiscovery.candidates.slice(0, limit),
      },
    };
  } catch (sdkError: unknown) {
    const sdkMessage = sdkError instanceof Error ? sdkError.message : String(sdkError);
    warnings.push(`Meteora SDK discovery failed: ${sdkMessage}`);
  }

  const manualDiscovery = await loadManualPools(projectRoot);
  const enrichment = await enrichCandidatesFromChain(connection, manualDiscovery.candidates);
  warnings.push(...enrichment.warnings);

  return {
    rawPayload: {
      discovery_method: "manual",
      warnings,
      manual_pools: manualDiscovery.raw,
    },
    result: {
      discovery_method: "manual",
      fetched_at_utc: new Date().toISOString(),
      warnings: [
        ...warnings,
        "Loaded pools from data/manual_pools.json because automated Meteora API/SDK discovery failed.",
      ],
      candidates: enrichment.candidates,
    },
  };
}

export const POOL_CANDIDATE_CSV_HEADERS = [
  "pool_address",
  "token_x_mint",
  "token_y_mint",
  "bin_step",
  "active_bin_id",
  "raw_name_or_symbol_if_available",
  "source",
  "fetched_at_utc",
] as const;

export function poolCandidatesToCsvRows(
  candidates: PoolCandidate[],
): Array<Record<string, string | number | "">> {
  return candidates.map((candidate) => ({
    pool_address: candidate.pool_address,
    token_x_mint: candidate.token_x_mint,
    token_y_mint: candidate.token_y_mint,
    bin_step: candidate.bin_step,
    active_bin_id: candidate.active_bin_id,
    raw_name_or_symbol_if_available: candidate.raw_name_or_symbol_if_available,
    source: candidate.source,
    fetched_at_utc: candidate.fetched_at_utc,
  }));
}
