const METEORA_DATAPI_BASE = "https://dlmm.datapi.meteora.ag";

export type OhlcvTimeframe = "5m" | "30m" | "1h" | "2h" | "4h" | "12h" | "24h";

export type OhlcvCandle = {
  timestamp: number;
  timestamp_str: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type OhlcvFetchResult = {
  pool_address: string;
  fetched_at_utc: string;
  timeframe: OhlcvTimeframe;
  start_time: number;
  end_time: number;
  chunk_count: number;
  data: OhlcvCandle[];
};

type OhlcvApiResponse = {
  start_time: number;
  end_time: number;
  timeframe: OhlcvTimeframe;
  data: OhlcvCandle[];
  message?: string;
};

/** Conservative max span per request (seconds) before Meteora returns "time range too large". */
const MAX_CHUNK_SECONDS: Record<OhlcvTimeframe, number> = {
  "5m": 8 * 3600,
  "30m": 2 * 24 * 3600,
  "1h": 3 * 24 * 3600,
  "2h": 7 * 24 * 3600,
  "4h": 14 * 24 * 3600,
  "12h": 30 * 24 * 3600,
  "24h": 60 * 24 * 3600,
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchOhlcvChunk(
  poolAddress: string,
  timeframe: OhlcvTimeframe,
  startTime: number,
  endTime: number,
): Promise<OhlcvApiResponse> {
  const url = new URL(`/pools/${poolAddress}/ohlcv`, METEORA_DATAPI_BASE);
  url.searchParams.set("timeframe", timeframe);
  url.searchParams.set("start_time", String(startTime));
  url.searchParams.set("end_time", String(endTime));

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Meteora OHLCV returned HTTP ${response.status}`);
  }

  const body = (await response.json()) as OhlcvApiResponse;
  if (body.message) {
    throw new Error(`Meteora OHLCV error: ${body.message}`);
  }

  return body;
}

export type FetchPoolOhlcvOptions = {
  timeframe?: OhlcvTimeframe;
  /** Unix seconds; defaults to now minus `lookbackDays`. */
  startTime?: number;
  /** Unix seconds; defaults to now. */
  endTime?: number;
  lookbackDays?: number;
};

export async function fetchPoolOhlcv(
  poolAddress: string,
  options?: FetchPoolOhlcvOptions,
): Promise<OhlcvFetchResult> {
  const fetchedAtUtc = new Date().toISOString();
  const timeframe = options?.timeframe ?? "1h";
  const endTime = options?.endTime ?? Math.floor(Date.now() / 1000);
  const lookbackDays = options?.lookbackDays ?? 7;
  const startTime =
    options?.startTime ?? endTime - lookbackDays * 24 * 3600;

  const chunkSpan = MAX_CHUNK_SECONDS[timeframe];
  const candles: OhlcvCandle[] = [];
  let chunkStart = startTime;
  let chunkCount = 0;

  while (chunkStart < endTime) {
    const chunkEnd = Math.min(chunkStart + chunkSpan, endTime);
    const chunk = await fetchOhlcvChunk(poolAddress, timeframe, chunkStart, chunkEnd);
    chunkCount += 1;
    candles.push(...chunk.data);

    if (chunkEnd >= endTime) {
      break;
    }

    chunkStart = chunkEnd;
    await sleep(120);
  }

  const byTimestamp = new Map<number, OhlcvCandle>();
  for (const candle of candles) {
    byTimestamp.set(candle.timestamp, candle);
  }

  const sorted = [...byTimestamp.values()].sort((a, b) => a.timestamp - b.timestamp);

  return {
    pool_address: poolAddress,
    fetched_at_utc: fetchedAtUtc,
    timeframe,
    start_time: startTime,
    end_time: endTime,
    chunk_count: chunkCount,
    data: sorted,
  };
}
