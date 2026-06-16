import { readFile } from "node:fs/promises";
import path from "node:path";

import type { ManualPoolsFile, PoolCandidate } from "./types.js";

const METEORA_DATAPI_BASE = "https://dlmm.datapi.meteora.ag";
const MAX_DATAPI_PAGES = 40;
const PAGE_SIZE = 100;

export type TrianglePreset = {
  id: string;
  tokens: Array<{ symbol: string; mint: string }>;
  fallback_id?: string;
  leg_pools?: Record<string, string>;
  leg_mints?: Record<string, { token_x_mint: string; token_y_mint: string }>;
};

export type ResolvedTriangleLeg = {
  pool_address: string;
  token_x_mint: string;
  token_y_mint: string;
  source: string;
  pool_name?: string;
  tvl?: number;
};

export type ResolvedTriangleLegs = Record<string, ResolvedTriangleLeg>;

type MeteoraApiPool = {
  address: string;
  name?: string;
  tvl?: number;
  token_x?: { address?: string };
  token_y?: { address?: string };
};

type MeteoraApiPoolsResponse = {
  data?: MeteoraApiPool[];
};

function legKey(symbolA: string, symbolB: string): string {
  return `${symbolA}-${symbolB}`;
}

function mintPairKey(mintA: string, mintB: string): string {
  return [mintA, mintB].sort().join(":");
}

function orderedLegs(
  tokens: TrianglePreset["tokens"],
): Array<{ key: string; mintA: string; mintB: string; symbolA: string; symbolB: string }> {
  const [a, b, c] = tokens;
  return [
    { key: legKey(a.symbol, b.symbol), mintA: a.mint, mintB: b.mint, symbolA: a.symbol, symbolB: b.symbol },
    { key: legKey(b.symbol, c.symbol), mintA: b.mint, mintB: c.mint, symbolA: b.symbol, symbolB: c.symbol },
    { key: legKey(c.symbol, a.symbol), mintA: c.mint, mintB: a.mint, symbolA: c.symbol, symbolB: a.symbol },
  ];
}

async function loadPoolCandidates(projectRoot: string): Promise<PoolCandidate[]> {
  const csvPath = path.join(projectRoot, "data", "processed", "pool_candidates.csv");
  try {
    const text = await readFile(csvPath, "utf8");
    const lines = text.trim().split("\n");
    if (lines.length < 2) {
      return [];
    }
    const headers = lines[0].split(",");
    const idx = (name: string) => headers.indexOf(name);
    const poolIdx = idx("pool_address");
    const xIdx = idx("token_x_mint");
    const yIdx = idx("token_y_mint");
    const nameIdx = idx("raw_name_or_symbol_if_available");

    return lines.slice(1).map((line) => {
      const cols = line.split(",");
      return {
        pool_address: cols[poolIdx] ?? "",
        token_x_mint: cols[xIdx] ?? "",
        token_y_mint: cols[yIdx] ?? "",
        bin_step: "",
        active_bin_id: "",
        raw_name_or_symbol_if_available: nameIdx >= 0 ? cols[nameIdx] ?? "" : "",
        source: "meteora_api" as const,
        fetched_at_utc: "",
      };
    });
  } catch {
    return [];
  }
}

async function loadManualPools(projectRoot: string): Promise<Map<string, string>> {
  const manualPath = path.join(projectRoot, "data", "manual_pools.json");
  const noteToAddress = new Map<string, string>();
  try {
    const raw = JSON.parse(await readFile(manualPath, "utf8")) as ManualPoolsFile;
    for (const pool of raw.pools) {
      if (!pool.note) {
        continue;
      }
      const normalized = pool.note.split(";")[0].trim().replace(/\s+/g, "").toUpperCase();
      noteToAddress.set(normalized, pool.pool_address);
    }
  } catch {
    // manual file optional
  }
  return noteToAddress;
}

function candidateMatchesMintPair(
  candidate: PoolCandidate,
  mintA: string,
  mintB: string,
): boolean {
  if (!candidate.token_x_mint || !candidate.token_y_mint) {
    return false;
  }
  return mintPairKey(candidate.token_x_mint, candidate.token_y_mint) === mintPairKey(mintA, mintB);
}

async function fetchDatapiPage(page: number): Promise<MeteoraApiPool[]> {
  const url = new URL("/pools", METEORA_DATAPI_BASE);
  url.searchParams.set("page_size", String(PAGE_SIZE));
  url.searchParams.set("current_page", String(page));
  url.searchParams.set("sort_key", "tvl");
  url.searchParams.set("order_by", "desc");

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Meteora datapi /pools returned HTTP ${response.status}`);
  }

  const raw = (await response.json()) as MeteoraApiPoolsResponse;
  return raw.data ?? [];
}

async function fetchDatapiPoolsByPair(
  mintA: string,
  mintB: string,
): Promise<MeteoraApiPool[]> {
  const matches: MeteoraApiPool[] = [];
  const targetPair = mintPairKey(mintA, mintB);

  for (let page = 1; page <= MAX_DATAPI_PAGES; page += 1) {
    const pools = await fetchDatapiPage(page);
    if (pools.length === 0) {
      break;
    }

    for (const pool of pools) {
      const mx = pool.token_x?.address ?? "";
      const my = pool.token_y?.address ?? "";
      if (mintPairKey(mx, my) === targetPair) {
        matches.push(pool);
      }
    }

    if (matches.length > 0) {
      break;
    }
  }

  return matches.sort((a, b) => (b.tvl ?? 0) - (a.tvl ?? 0));
}

async function lookupPoolMints(
  poolAddress: string,
  candidates: PoolCandidate[],
): Promise<{ token_x_mint: string; token_y_mint: string } | null> {
  const match = candidates.find((c) => c.pool_address === poolAddress);
  if (match?.token_x_mint && match?.token_y_mint) {
    return { token_x_mint: match.token_x_mint, token_y_mint: match.token_y_mint };
  }

  for (let page = 1; page <= MAX_DATAPI_PAGES; page += 1) {
    const pools = await fetchDatapiPage(page);
    if (pools.length === 0) {
      break;
    }
    const found = pools.find((p) => p.address === poolAddress);
    if (found?.token_x?.address && found?.token_y?.address) {
      return {
        token_x_mint: found.token_x.address,
        token_y_mint: found.token_y.address,
      };
    }
  }
  return null;
}

function manualKeyForLeg(symbolA: string, symbolB: string): string {
  return `${symbolA}-${symbolB}`.toUpperCase();
}

export async function resolveTriangleLegs(
  preset: TrianglePreset,
  projectRoot: string,
): Promise<ResolvedTriangleLegs> {
  const candidates = await loadPoolCandidates(projectRoot);
  const manualNotes = await loadManualPools(projectRoot);
  const legs = orderedLegs(preset.tokens);
  const resolved: ResolvedTriangleLegs = {};

  for (const leg of legs) {
    const override = preset.leg_pools?.[leg.key];
    if (override) {
      const mintInfo =
        preset.leg_mints?.[leg.key] ?? (await lookupPoolMints(override, candidates));
      resolved[leg.key] = {
        pool_address: override,
        token_x_mint: mintInfo?.token_x_mint ?? "",
        token_y_mint: mintInfo?.token_y_mint ?? "",
        source: "override",
      };
      continue;
    }

    const manualAddress =
      manualNotes.get(manualKeyForLeg(leg.symbolA, leg.symbolB)) ??
      manualNotes.get(manualKeyForLeg(leg.symbolB, leg.symbolA));
    if (manualAddress) {
      const mintInfo =
        preset.leg_mints?.[leg.key] ??
        (await lookupPoolMints(manualAddress, candidates));
      resolved[leg.key] = {
        pool_address: manualAddress,
        token_x_mint: mintInfo?.token_x_mint ?? "",
        token_y_mint: mintInfo?.token_y_mint ?? "",
        source: "manual",
      };
      continue;
    }

    const candidateMatches = candidates.filter((c) =>
      candidateMatchesMintPair(c, leg.mintA, leg.mintB),
    );
    if (candidateMatches.length > 0) {
      const best = candidateMatches[0];
      resolved[leg.key] = {
        pool_address: best.pool_address,
        token_x_mint: best.token_x_mint,
        token_y_mint: best.token_y_mint,
        source: "pool_candidates",
        pool_name: best.raw_name_or_symbol_if_available,
      };
      continue;
    }

    const datapiMatches = await fetchDatapiPoolsByPair(leg.mintA, leg.mintB);
    if (datapiMatches.length > 0) {
      const best = datapiMatches[0];
      resolved[leg.key] = {
        pool_address: best.address,
        token_x_mint: best.token_x?.address ?? "",
        token_y_mint: best.token_y?.address ?? "",
        source: "datapi",
        pool_name: best.name,
        tvl: best.tvl,
      };
      continue;
    }

    resolved[leg.key] = {
      pool_address: "",
      token_x_mint: "",
      token_y_mint: "",
      source: "missing",
    };
  }

  return resolved;
}

export async function loadTrianglePreset(presetPath: string): Promise<TrianglePreset> {
  return JSON.parse(await readFile(presetPath, "utf8")) as TrianglePreset;
}
