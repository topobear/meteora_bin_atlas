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

export type ProcessedPoolSnapshot = {
  pool_address: string;
  active_bin_id: number;
  active_bin_price: string;
  token_x_mint: string;
  token_y_mint: string;
  bin_step: number;
  fetched_at_utc: string;
};

export type RawPoolSnapshot = {
  pool_address: string;
  fetched_at_utc: string;
  method: string;
  lb_pair: unknown;
  active_bin: unknown;
  token_x: unknown;
  token_y: unknown;
};

export type PoolStateFetchResult = {
  processed: ProcessedPoolSnapshot;
  raw: RawPoolSnapshot;
};

export type BinArraysFetchMethod = "getBinArrays" | "getBinsAroundActiveBin";

export type BinArraysFetchResult = {
  pool_address: string;
  fetched_at_utc: string;
  method: BinArraysFetchMethod;
  raw: unknown;
  summary?: {
    bin_array_count?: number;
    bin_count?: number;
    active_bin_id?: number;
    bins_left?: number;
    bins_right?: number;
  };
};
