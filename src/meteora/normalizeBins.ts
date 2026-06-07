import { MAX_BIN_ARRAY_SIZE } from "@meteora-ag/dlmm";

import type { BinArraysFetchMethod } from "./types.js";

const BINS_PER_ARRAY = MAX_BIN_ARRAY_SIZE.toNumber();

export type RawBinArraysFile = {
  pool_address: string;
  fetched_at_utc: string;
  method: BinArraysFetchMethod;
  summary?: {
    active_bin_id?: number;
  };
  raw: unknown;
};

export type BinAtlasRow = {
  pool_address: string;
  fetched_at_utc: string;
  bin_array_index: number | "";
  bin_id: number;
  distance_from_active: number | "";
  price: string;
  price_per_token: string;
  liquidity: string;
  x_amount: string;
  y_amount: string;
  composition_y: number | "";
  is_active_bin: boolean;
  raw_bin_array_pubkey: string;
  raw_fields_json: string;
};

export const BIN_ATLAS_CSV_HEADERS = [
  "pool_address",
  "fetched_at_utc",
  "bin_array_index",
  "bin_id",
  "distance_from_active",
  "price",
  "price_per_token",
  "liquidity",
  "x_amount",
  "y_amount",
  "composition_y",
  "is_active_bin",
  "raw_bin_array_pubkey",
  "raw_fields_json",
] as const;

type OnChainBin = {
  amountX?: string;
  amountY?: string;
  price?: string;
  liquiditySupply?: string;
};

type BinLiquidityBin = {
  binId?: number;
  xAmount?: string;
  yAmount?: string;
  supply?: string;
  price?: string;
  pricePerToken?: string;
};

function binIdFromArrayPosition(binArrayIndex: number, position: number): number {
  return binArrayIndex * BINS_PER_ARRAY + position;
}

function binArrayIndexFromBinId(binId: number): number {
  const idx = Math.trunc(binId / BINS_PER_ARRAY);
  const mod = binId % BINS_PER_ARRAY;
  return binId < 0 && mod !== 0 ? idx - 1 : idx;
}

function computeCompositionY(xAmount: string, yAmount: string): number | "" {
  const x = BigInt(xAmount || "0");
  const y = BigInt(yAmount || "0");
  const total = x + y;

  if (total === 0n) {
    return "";
  }

  const scaled = (y * 1_000_000n) / total;
  return Number(scaled) / 1_000_000;
}

function buildRow(params: {
  poolAddress: string;
  fetchedAtUtc: string;
  activeBinId: number | undefined;
  binArrayIndex: number | "";
  binId: number;
  price: string;
  pricePerToken: string;
  liquidity: string;
  xAmount: string;
  yAmount: string;
  rawBinArrayPubkey: string;
  rawFields: unknown;
}): BinAtlasRow {
  const distanceFromActive =
    params.activeBinId !== undefined ? params.binId - params.activeBinId : "";

  return {
    pool_address: params.poolAddress,
    fetched_at_utc: params.fetchedAtUtc,
    bin_array_index: params.binArrayIndex,
    bin_id: params.binId,
    distance_from_active: distanceFromActive,
    price: params.price,
    price_per_token: params.pricePerToken,
    liquidity: params.liquidity,
    x_amount: params.xAmount,
    y_amount: params.yAmount,
    composition_y: computeCompositionY(params.xAmount, params.yAmount),
    is_active_bin: params.activeBinId !== undefined && params.binId === params.activeBinId,
    raw_bin_array_pubkey: params.rawBinArrayPubkey,
    raw_fields_json: JSON.stringify(params.rawFields),
  };
}

function normalizeGetBinArrays(
  file: RawBinArraysFile,
  activeBinId: number | undefined,
): BinAtlasRow[] {
  const rows: BinAtlasRow[] = [];
  const raw = file.raw;

  if (!Array.isArray(raw)) {
    throw new Error("Expected raw getBinArrays payload to be an array.");
  }

  for (const binArrayAccount of raw) {
    const account = (binArrayAccount as { account?: { index?: string; bins?: OnChainBin[] } })
      .account;
    const publicKey = (binArrayAccount as { publicKey?: string }).publicKey ?? "";

    if (!account?.bins || account.index === undefined) {
      continue;
    }

    const binArrayIndex = Number(account.index);
    if (!Number.isFinite(binArrayIndex)) {
      continue;
    }

    account.bins.forEach((bin, position) => {
      const binId = binIdFromArrayPosition(binArrayIndex, position);

      rows.push(
        buildRow({
          poolAddress: file.pool_address,
          fetchedAtUtc: file.fetched_at_utc,
          activeBinId,
          binArrayIndex,
          binId,
          price: bin.price ?? "",
          pricePerToken: "",
          liquidity: bin.liquiditySupply ?? "",
          xAmount: bin.amountX ?? "",
          yAmount: bin.amountY ?? "",
          rawBinArrayPubkey: publicKey,
          rawFields: bin,
        }),
      );
    });
  }

  return rows;
}

function normalizeBinsAroundActiveBin(
  file: RawBinArraysFile,
  activeBinId: number | undefined,
): BinAtlasRow[] {
  const raw = file.raw as { activeBin?: number; bins?: BinLiquidityBin[] } | null;

  if (!raw?.bins || !Array.isArray(raw.bins)) {
    throw new Error("Expected raw getBinsAroundActiveBin payload to include bins.");
  }

  const resolvedActiveBinId = activeBinId ?? raw.activeBin;

  return raw.bins.map((bin) => {
    const binId = bin.binId;
    if (binId === undefined) {
      throw new Error("Bounded bin payload is missing binId.");
    }

    return buildRow({
      poolAddress: file.pool_address,
      fetchedAtUtc: file.fetched_at_utc,
      activeBinId: resolvedActiveBinId,
      binArrayIndex: binArrayIndexFromBinId(binId),
      binId,
      price: bin.price ?? "",
      pricePerToken: bin.pricePerToken ?? "",
      liquidity: bin.supply ?? "",
      xAmount: bin.xAmount ?? "",
      yAmount: bin.yAmount ?? "",
      rawBinArrayPubkey: "",
      rawFields: bin,
    });
  });
}

export function normalizeBinArrays(file: RawBinArraysFile): BinAtlasRow[] {
  const activeBinId = file.summary?.active_bin_id;

  if (file.method === "getBinArrays") {
    return normalizeGetBinArrays(file, activeBinId);
  }

  if (file.method === "getBinsAroundActiveBin") {
    return normalizeBinsAroundActiveBin(file, activeBinId);
  }

  throw new Error(`Unsupported bin arrays method: ${String(file.method)}`);
}

export function binAtlasRowsToCsvRows(
  rows: BinAtlasRow[],
): Array<Record<string, string | number | "">> {
  return rows.map((row) => ({
    pool_address: row.pool_address,
    fetched_at_utc: row.fetched_at_utc,
    bin_array_index: row.bin_array_index,
    bin_id: row.bin_id,
    distance_from_active: row.distance_from_active,
    price: row.price,
    price_per_token: row.price_per_token,
    liquidity: row.liquidity,
    x_amount: row.x_amount,
    y_amount: row.y_amount,
    composition_y: row.composition_y,
    is_active_bin: row.is_active_bin ? "true" : "false",
    raw_bin_array_pubkey: row.raw_bin_array_pubkey,
    raw_fields_json: row.raw_fields_json,
  }));
}
