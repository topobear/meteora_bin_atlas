import DLMM from "@meteora-ag/dlmm";
import { Connection, PublicKey } from "@solana/web3.js";

import { serializeForJson } from "../io/serialize.js";
import type { BinArraysFetchResult } from "./types.js";

export type DlmmPool = Awaited<ReturnType<typeof DLMM.create>>;

const DEFAULT_BINS_LEFT = 20;
const DEFAULT_BINS_RIGHT = 20;

export type FetchBinArraysOptions = {
  bounded?: {
    left: number;
    right: number;
  };
};

async function fetchWithGetBinArrays(
  connection: Connection,
  poolAddress: string,
  fetchedAtUtc: string,
): Promise<BinArraysFetchResult> {
  const poolPubkey = new PublicKey(poolAddress);
  const dlmmPool = await DLMM.create(connection, poolPubkey);
  await dlmmPool.refetchStates();

  const binArrays = await dlmmPool.getBinArrays();

  return {
    pool_address: poolPubkey.toBase58(),
    fetched_at_utc: fetchedAtUtc,
    method: "getBinArrays",
    raw: serializeForJson(binArrays),
    summary: {
      bin_array_count: binArrays.length,
      active_bin_id: dlmmPool.lbPair.activeId,
    },
  };
}

export async function fetchBoundedBinsFromPool(
  dlmmPool: DlmmPool,
  poolAddress: string,
  left: number,
  right: number,
  fetchedAtUtc = new Date().toISOString(),
): Promise<BinArraysFetchResult> {
  await dlmmPool.refetchStates();
  const result = await dlmmPool.getBinsAroundActiveBin(left, right);

  return {
    pool_address: poolAddress,
    fetched_at_utc: fetchedAtUtc,
    method: "getBinsAroundActiveBin",
    raw: serializeForJson(result),
    summary: {
      bin_count: result.bins.length,
      active_bin_id: result.activeBin,
      bins_left: left,
      bins_right: right,
    },
  };
}

async function fetchWithBinsAroundActiveBin(
  connection: Connection,
  poolAddress: string,
  fetchedAtUtc: string,
  left: number,
  right: number,
): Promise<BinArraysFetchResult> {
  const poolPubkey = new PublicKey(poolAddress);
  const dlmmPool = await DLMM.create(connection, poolPubkey);

  return fetchBoundedBinsFromPool(dlmmPool, poolPubkey.toBase58(), left, right, fetchedAtUtc);
}

export async function fetchBinArrays(
  connection: Connection,
  poolAddress: string,
  options?: FetchBinArraysOptions,
): Promise<BinArraysFetchResult> {
  const fetchedAtUtc = new Date().toISOString();

  if (options?.bounded) {
    return fetchWithBinsAroundActiveBin(
      connection,
      poolAddress,
      fetchedAtUtc,
      options.bounded.left,
      options.bounded.right,
    );
  }

  try {
    return await fetchWithGetBinArrays(connection, poolAddress, fetchedAtUtc);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(
      `getBinArrays failed (${message}); falling back to getBinsAroundActiveBin(${DEFAULT_BINS_LEFT}, ${DEFAULT_BINS_RIGHT}).`,
    );

    return fetchWithBinsAroundActiveBin(
      connection,
      poolAddress,
      fetchedAtUtc,
      DEFAULT_BINS_LEFT,
      DEFAULT_BINS_RIGHT,
    );
  }
}
