# Meteora bin atlas — Makefile shortcuts for the TypeScript fetch pipeline.
#
# Two main workflows:
#
#   make atlas     Single point-in-time bin atlas (discover → fetch → normalize).
#                  Use for static notebook plots of current liquidity shape.
#                  Output: data/processed/bin_atlas_<pool>_<ts>.csv
#
#   make temporal  Multi-snapshot sample for animation (OHLCV + live series).
#                  Meteora datapi has price history; per-bin liquidity must be
#                  polled from Solana RPC. Defaults are slow for public RPC.
#                  Output: pool_ohlcv_*.json + bin_atlas_series_*.csv
#                  (~14 min wall time: 10 snapshots × 90s pause)
#
# Override pool:  make atlas POOL=<address>
# Bounded bins:   make fetch-bins BOUNDED=1 BINS_LEFT=30 BINS_RIGHT=30

.PHONY: help install install-ts install-py smoke discover fetch-pool fetch-bins normalize-bins \
	fetch-ohlcv fetch-series normalize-series temporal atlas notebook

# Default pool: SOL-USDC from data/manual_pools.json
POOL ?= 5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6

# --- Single-snapshot (make atlas) -------------------------------------------

# Neighborhood width when BOUNDED=1; ignored for full-pool fetch (make atlas default).
BINS_LEFT ?= 30
BINS_RIGHT ?= 30
BOUNDED ?=

# --- Temporal (make temporal) -----------------------------------------------

# Price candles from Meteora datapi (no Solana RPC).
OHLCV_TIMEFRAME ?= 1h
OHLCV_LOOKBACK_DAYS ?= 7

# Live snapshot series from Solana RPC.
# Waits between snapshots: RPC backoff first (rate-limit cushion), then interval.
SERIES_COUNT ?= 10
SERIES_RPC_BACKOFF_SEC ?= 60
SERIES_INTERVAL_SEC ?= 30
SERIES_BINS_LEFT ?= 30
SERIES_BINS_RIGHT ?= 30

# --- Shared CLI args --------------------------------------------------------

POOL_ARGS = --pool $(POOL)
BINS_BOUNDED_ARGS = $(if $(BOUNDED),--bounded --bins-left $(BINS_LEFT) --bins-right $(BINS_RIGHT),)
TEMPORAL_ARGS = $(POOL_ARGS) \
	--timeframe $(OHLCV_TIMEFRAME) --lookback-days $(OHLCV_LOOKBACK_DAYS) \
	--count $(SERIES_COUNT) --rpc-backoff-sec $(SERIES_RPC_BACKOFF_SEC) --interval-sec $(SERIES_INTERVAL_SEC) \
	--bins-left $(SERIES_BINS_LEFT) --bins-right $(SERIES_BINS_RIGHT)

# --- Setup ------------------------------------------------------------------

help:
	@echo "Meteora bin atlas — common targets"
	@echo ""
	@echo "Setup"
	@echo "  make install          npm + poetry install"
	@echo "  make install-ts       npm install"
	@echo "  make install-py       poetry install"
	@echo "  make smoke            Solana RPC smoke test"
	@echo ""
	@echo "Single snapshot (POOL=$(POOL))"
	@echo "  make discover         discover pool candidates"
	@echo "  make fetch-pool       pool state + active bin"
	@echo "  make fetch-bins       bin arrays (set BOUNDED=1 for neighborhood)"
	@echo "  make normalize-bins   bin atlas CSV"
	@echo "  make atlas            discover + fetch-pool + fetch-bins + normalize-bins"
	@echo ""
	@echo "Temporal sample (for animation)"
	@echo "  make temporal           OHLCV + snapshot series + series CSV (one command)"
	@echo "  make fetch-ohlcv        price candles only"
	@echo "  make fetch-series       bounded snapshot series only"
	@echo "  make normalize-series   normalize latest series manifest only"
	@echo ""
	@echo "Temporal knobs: OHLCV_TIMEFRAME, OHLCV_LOOKBACK_DAYS,"
	@echo "  SERIES_COUNT, SERIES_RPC_BACKOFF_SEC, SERIES_INTERVAL_SEC, SERIES_BINS_LEFT/RIGHT"
	@echo ""
	@echo "Notebook"
	@echo "  make notebook         launch Jupyter notebook"
	@echo ""
	@echo "Override pool: make atlas POOL=<address>"

install: install-ts install-py

install-ts:
	npm install

install-py:
	poetry install

smoke:
	npm run smoke

# --- Single-snapshot pipeline -------------------------------------------------

# Meteora datapi / SDK / manual fallback → data/processed/pool_candidates.csv
discover:
	npm run discover:pools

# Active bin, token mints, bin step → data/processed/pool_snapshot_<pool>_<ts>.json
fetch-pool:
	npm run fetch:pool -- $(POOL_ARGS)

# Full bin arrays by default; BOUNDED=1 fetches ±N bins around active bin only.
fetch-bins:
	npm run fetch:bins -- $(POOL_ARGS) $(BINS_BOUNDED_ARGS)

# Latest raw bin_arrays → data/processed/bin_atlas_<pool>_<ts>.csv (one row per bin)
normalize-bins:
	npm run normalize:bins -- $(POOL_ARGS)

# End-to-end single snapshot. ~10–30s on a healthy RPC (full pool can be slower).
atlas: discover fetch-pool fetch-bins normalize-bins

# --- Temporal pipeline ------------------------------------------------------

# OHLCV + bounded snapshot series + series CSV in one npm script.
temporal:
	npm run temporal -- $(TEMPORAL_ARGS)

# Meteora datapi only; fast (~seconds).
fetch-ohlcv:
	npm run fetch:ohlcv -- $(POOL_ARGS) --timeframe $(OHLCV_TIMEFRAME) --lookback-days $(OHLCV_LOOKBACK_DAYS)

# Repeated bounded bin fetches; slow on public RPC (backoff + interval between snaps).
fetch-series:
	npm run fetch:series -- $(POOL_ARGS) --count $(SERIES_COUNT) --rpc-backoff-sec $(SERIES_RPC_BACKOFF_SEC) --interval-sec $(SERIES_INTERVAL_SEC) --bins-left $(SERIES_BINS_LEFT) --bins-right $(SERIES_BINS_RIGHT)

# Latest snapshot_series manifest → data/processed/bin_atlas_series_<pool>_<ts>.csv
normalize-series:
	npm run normalize:series -- $(POOL_ARGS)

# --- Notebook ---------------------------------------------------------------

notebook:
	poetry run jupyter notebook notebooks/01_connect_fetch_explore_meteora.ipynb
