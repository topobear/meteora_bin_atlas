export type DiscoverySource = "meteora_api" | "meteora_sdk" | "manual";

export type PoolCandidate = {
  pool_address: string;
  token_x_mint: string;
  token_y_mint: string;
  bin_step: number | "";
  active_bin_id: number | "";
  raw_name_or_symbol_if_available: string;
  source: DiscoverySource;
  fetched_at_utc: string;
};

export type ManualPoolsFile = {
  source: "manual";
  note: string;
  pools: Array<{
    pool_address: string;
    note?: string;
  }>;
};

export type PoolDiscoveryResult = {
  discovery_method: DiscoverySource;
  fetched_at_utc: string;
  warnings: string[];
  candidates: PoolCandidate[];
};
