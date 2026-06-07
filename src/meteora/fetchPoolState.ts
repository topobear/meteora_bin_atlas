import DLMM from "@meteora-ag/dlmm";
import { Connection, PublicKey } from "@solana/web3.js";

import { serializeForJson } from "../io/serialize.js";
import type { PoolStateFetchResult } from "./types.js";

export async function fetchPoolState(
  connection: Connection,
  poolAddress: string,
): Promise<PoolStateFetchResult> {
  const fetchedAtUtc = new Date().toISOString();
  const poolPubkey = new PublicKey(poolAddress);

  const dlmmPool = await DLMM.create(connection, poolPubkey);
  await dlmmPool.refetchStates();
  const activeBin = await dlmmPool.getActiveBin();

  const raw = {
    pool_address: poolPubkey.toBase58(),
    fetched_at_utc: fetchedAtUtc,
    method: "DLMM.create + refetchStates + getActiveBin",
    lb_pair: serializeForJson(dlmmPool.lbPair),
    active_bin: serializeForJson(activeBin),
    token_x: serializeForJson({
      public_key: dlmmPool.tokenX.publicKey,
      reserve: dlmmPool.tokenX.reserve,
      mint: dlmmPool.tokenX.mint,
      amount: dlmmPool.tokenX.amount,
      owner: dlmmPool.tokenX.owner,
    }),
    token_y: serializeForJson({
      public_key: dlmmPool.tokenY.publicKey,
      reserve: dlmmPool.tokenY.reserve,
      mint: dlmmPool.tokenY.mint,
      amount: dlmmPool.tokenY.amount,
      owner: dlmmPool.tokenY.owner,
    }),
  };

  const processed = {
    pool_address: poolPubkey.toBase58(),
    active_bin_id: activeBin.binId,
    active_bin_price: activeBin.pricePerToken,
    token_x_mint: dlmmPool.lbPair.tokenXMint.toBase58(),
    token_y_mint: dlmmPool.lbPair.tokenYMint.toBase58(),
    bin_step: dlmmPool.lbPair.binStep,
    fetched_at_utc: fetchedAtUtc,
  };

  return { processed, raw };
}
