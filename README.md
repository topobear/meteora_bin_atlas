# Meteora Bin Atlas

A small **read-only research sketchbook** for exploring Meteora DLMM bin-level liquidity on Solana.

This is not a trading bot, dashboard, Solana indexer, or production analytics product. It is a compact atlas of Meteora bins: connect via RPC, fetch pool/bin data, normalize snapshots, and visualize liquidity around the active bin.

## Current status

*Paused 2026-06-07 · branch `2026-06-07-nwqg` · example pool SOL-USDC (`5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6`)*

### Working end-to-end

| Pipeline | Command | Status |
|----------|---------|--------|
| Static atlas | `make atlas` | Steps 0–6 done — discover, fetch pool/bins, normalize CSV, Jupyter notebook |
| Temporal / animation | `make poll-snapshots` → `make render-mp4` | Done once on SOL-USDC (10 bounded snapshots, ~14 min) |

**Artifacts from the completed temporal run:**

- `data/processed/bin_atlas_series_5rCf1DM8…_2026-06-07T08-18-59-514Z.csv`
- `plots/temporal_5rCf1DM8…_2026-06-07T08-18-59-514Z.mp4`
- `data/processed/pool_ohlcv_5rCf1DM8…_1h_2026-06-07T08-05-26-849Z.json`

RPC defaults to Solana Foundation (no signup); bounded fetches are ~1s each. See [Improvements](#improvements) for endpoint benchmarks and faster polling knobs (`SERIES_RPC_BACKOFF_SEC`, `SERIES_INTERVAL_SEC`).

### Left off / not done

- **GAMEPLAN Step 7** — lightweight bin-distribution metrics (`bin_metrics_*.csv`, notebook section). Next planned feature work.
- **GAMEPLAN Steps 8+** — snapshot comparison in notebook, public artifact compression, read-only swap quotes.
- **Currency triangle seed** — `data/triangles/sol_usdc_usdt.json` plus `make currency-triangle` materializes the first SOL/USDC/USDT routing-intelligence object.
- **37 extra raw snapshots** in `data/raw/` from ~11:42 UTC (rapid bounded-fetch experiments, ~1s apart) — not tied to a `snapshot_series` manifest; safe to ignore or delete before the next poll.
- **Small cleanup** — `make temporal` alias (matches `npm run temporal`); RPC/backoff comments in `.env.example`.

### Resume here

```bash
cp .env.example .env   # set SOLANA_RPC_URL if needed
make install
make smoke             # confirm RPC

# Re-run temporal pipeline (faster example: ~2.5 min for 10 snapshots)
make poll-snapshots SERIES_RPC_BACKOFF_SEC=5 SERIES_INTERVAL_SEC=10
make render-mp4

# Or seed the first currency-triangle intelligence object
make currency-triangle
# Optional: attach Jupiter quote samples after setting JUPITER_API_KEY
make currency-triangle WITH_JUPITER=1

# Or static single snapshot + notebook
make atlas
make notebook
```

Theory notes added this session: [notes/dlmm_theory.md](notes/dlmm_theory.md) (V2/V3 → DLMM coordinate chart). Step-by-step plan: [GAMEPLAN.md](GAMEPLAN.md).

## What it does

- Connects to Solana through an RPC URL (no local node required)
- Discovers or selects Meteora DLMM pools
- Fetches pool state and bin arrays
- Normalizes bins into a tidy atlas table
- Visualizes liquidity around the active bin in Jupyter

## What it does not do

- Does not trade or submit transactions
- Does not require a wallet
- Does not run a Solana node
- Does not make trading recommendations

## Key definitions

**Bin** — A discrete price cell in a DLMM pool. Each bin holds liquidity at one fixed price; trades within a bin execute at that price until liquidity is consumed and the pool moves to the next bin.

**Active bin** — The single bin where the current market price sits and swaps execute now. Stored as an active bin ID on the pool (`LbPair`) account.

**Bin step** — Spacing between neighboring bins, in basis points. Smaller steps mean tighter price increments; larger steps mean wider jumps between bins.

See [notes/dlmm_notes.md](notes/dlmm_notes.md) for fuller domain notes and source-backed explanations.

## Quickstart

### TypeScript (RPC smoke test)

```bash
cp .env.example .env
make install
make smoke
```

Or without Make: `npm install` and `npm run smoke`. Run `make help` for all targets.

Set `SOLANA_RPC_URL` in `.env` to a private RPC endpoint for reliable mainnet reads. The smoke script prints only the RPC hostname, not API keys.

### Python / Jupyter

```bash
poetry install
poetry run jupyter notebook notebooks/01_connect_fetch_explore_meteora.ipynb
```

The notebook runs the TypeScript pipeline via shell commands, loads the bin atlas CSV, and plots liquidity around the active bin.

## Project layout

```text
src/           TypeScript pipeline (config, Solana connection, scripts)
notebooks/     Jupyter research notebooks
data/raw/      Raw fetched snapshots
data/processed/ Normalized tables
plots/         Saved figures
notes/         Domain notes and research log
```

## Sources

### Meteora DLMM

- [Meteora docs](https://docs.meteora.ag/)
- [What is DLMM?](https://docs.meteora.ag/core-products/dlmm/what-is-dlmm.md)
- [DLMM TypeScript SDK](https://docs.meteora.ag/developer-guides/dlmm/typescript-sdk/sdk-functions.md)
- [DLMM SDK (GitHub)](https://github.com/MeteoraAg/dlmm-sdk)

### Liquidity Book lineage

- [LFJ Liquidity Book whitepaper repo](https://github.com/lfj-gg/LB-Whitepaper)
- [Whitepaper PDF](https://github.com/lfj-gg/LB-Whitepaper/blob/main/Joe%20v2%20Liquidity%20Book%20Whitepaper.pdf)

### Solana

- [web3.js](https://solana-labs.github.io/solana-web3.js/)
- [RPC HTTP methods](https://solana.com/docs/rpc/http)

## Pipeline reference

- **Step 0** — TypeScript + Poetry skeleton, Solana RPC smoke test (`npm run smoke`)
- **Step 1** — Domain notes and README (this document)
- **Step 2** — Pool discovery (`npm run discover:pools` → `data/raw/pools_<timestamp>.json`, `data/processed/pool_candidates.csv`)
- **Step 3** — Pool snapshot (`npm run fetch:pool -- --pool <ADDRESS>` → `data/processed/pool_snapshot_<pool>_<timestamp>.json`)
- **Step 4** — Bin arrays (`npm run fetch:bins -- --pool <ADDRESS>` → `data/raw/bin_arrays_<pool>_<timestamp>.json`)
- **Step 5** — Bin atlas normalization (`npm run normalize:bins -- --pool <ADDRESS>` → `data/processed/bin_atlas_<pool>_<timestamp>.csv`)
- **Step 6** — Jupyter notebook (`notebooks/01_connect_fetch_explore_meteora.ipynb`)
- **Temporal** — OHLCV + snapshot series + series CSV + MP4 (`make poll-snapshots`, `make render-mp4`; scripts `fetch:ohlcv`, `fetch:series`, `normalize:series`, `temporal`)
- **Currency triangle** — SOL/USDC/USDT directed probes (`make currency-triangle`; optional `WITH_JUPITER=1` for Jupiter quote samples)
- **Next (GAMEPLAN Step 7)** — Lightweight bin-distribution metrics

### Pool discovery

```bash
npm run discover:pools
```

Discovery order: Meteora datapi (`https://dlmm.datapi.meteora.ag/pools`), then SDK `DLMM.getLbPairs`, then `data/manual_pools.json`. The legacy `https://dlmm-api.meteora.ag/pair/all` endpoint is checked but currently returns 404; datapi is used instead.

### Pool snapshot

```bash
npm run fetch:pool -- --pool 5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6
```

Or set `METEORA_POOL_ADDRESS` in `.env`. Uses `DLMM.create`, `refetchStates`, and `getActiveBin` via Solana RPC.

### Bin arrays

```bash
npm run fetch:bins -- --pool 5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6
```

Fetches all bin arrays via `getBinArrays()` by default. Use `--bounded` (optional `--bins-left N --bins-right N`) for a neighborhood around the active bin. Falls back to bounded fetch automatically if full `getBinArrays()` fails.

### Normalize bin atlas

```bash
npm run normalize:bins -- --pool 5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6
```

Reads the latest `data/raw/bin_arrays_<pool>_*.json` (or pass `--input <path>`) and writes a flat CSV with one row per bin.

### Poll snapshots (for animation)

Meteora datapi has **price/volume history** but not historical per-bin liquidity. Poll repeated on-chain snapshots:

```bash
# One command: OHLCV + snapshot series + series CSV
# Defaults: SOL-USDC pool, 7d hourly OHLCV + 10 snapshots (60s RPC backoff, then 30s interval, ~14 min)
make poll-snapshots

# Another pool
make poll-snapshots POOL=<ADDRESS>

# Step by step if needed
make fetch-ohlcv
make fetch-series
make normalize-series
make render-mp4
```

Outputs: `data/processed/pool_ohlcv_<pool>_<tf>_*.json` and `data/processed/bin_atlas_series_<pool>_*.csv`.

## Improvements

Ideas and known bottlenecks from running the temporal / snapshot-polling pipeline.

### RPC endpoints (free, no signup)

Default in `.env.example` is Solana Foundation (`https://api.mainnet-beta.solana.com`). For bounded bin fetches (`getBinsAroundActiveBin`), it is the fastest no-signup option that works with the Meteora DLMM SDK (~1s per snapshot).

| Endpoint | Works with DLMM? | Notes |
|----------|------------------|-------|
| Solana Foundation (default) | Yes | Fastest per-fetch; handles rapid bounded reads well |
| [Solana Tracker](https://rpc.solanatracker.io/public) | Yes | No signup; ~2–3× slower per snapshot |
| Ankr `https://rpc.ankr.com/solana` | No | Requires API key (403) |
| PublicNode | No | Blocks some DLMM SDK filtered RPC calls (403) |
| dRPC / Alchemy demo | No | Free tier blocks or demo key unavailable |

**Best free upgrade (signup):** [Helius](https://helius.dev) or [QuickNode](https://quicknode.com) free tier — higher rate limits and more reliable for repeated polling. Set `SOLANA_RPC_URL` in `.env`.

### Temporal polling speed

The main wall-clock cost of `make poll-snapshots` is not RPC latency but the default pauses between snapshots: `SERIES_RPC_BACKOFF_SEC=60` + `SERIES_INTERVAL_SEC=30` → 90s between each of 10 snapshots (~14 min total). Each bounded fetch itself takes ~1s on Foundation RPC; 20 rapid back-to-back fetches succeeded without rate limits, so the 60s backoff is conservative for bounded mode.

Faster runs without changing RPC:

```bash
make poll-snapshots SERIES_RPC_BACKOFF_SEC=5 SERIES_INTERVAL_SEC=10   # ~2.5 min for 10 snapshots
make poll-snapshots SERIES_RPC_BACKOFF_SEC=0 SERIES_INTERVAL_SEC=5    # tighter; watch for 429s
```

### Other

- Add `make temporal` as an alias for `make poll-snapshots` (matches `npm run temporal`).
- Document RPC choice and backoff knobs in `.env.example` comments.
