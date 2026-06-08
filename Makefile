# Meteora bin atlas — Makefile shortcuts for the TypeScript fetch pipeline.
#
# Two main workflows:
#
#   make atlas     Single point-in-time bin atlas (discover → fetch → normalize).
#                  Use for static notebook plots of current liquidity shape.
#                  Output: data/processed/bin_atlas_<pool>_<ts>.csv
#
#   make poll-snapshots  Multi-snapshot sample for animation (OHLCV + live series).
#                  Meteora datapi has price history; per-bin liquidity must be
#                  polled from Solana RPC. Defaults are slow for public RPC.
#                  Output: pool_ohlcv_*.json + bin_atlas_series_*.csv
#                  (~14 min wall time: 10 snapshots × 90s pause)
#
# Override pool:  make atlas POOL=<address>  (default: SOL-USDC)
# Bounded bins:   make fetch-bins BOUNDED=1 BINS_LEFT=30 BINS_RIGHT=30

.PHONY: help install install-ts install-py smoke discover fetch-pool fetch-bins normalize-bins \
	fetch-ohlcv fetch-series normalize-series poll-snapshots temporal simulate-series render-mp4 render-mp4-demo atlas notebook

# Default pool: SOL-USDC from data/manual_pools.json
POOL ?= 5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6

# --- Single-snapshot (make atlas) -------------------------------------------

# Neighborhood width when BOUNDED=1; ignored for full-pool fetch (make atlas default).
BINS_LEFT ?= 30
BINS_RIGHT ?= 30
BOUNDED ?=

# --- Snapshot polling (make poll-snapshots) ---------------------------------

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
POLL_SNAPSHOTS_ARGS = $(POOL_ARGS) \
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
	@echo "Temporal (default pool: SOL-USDC)"
	@echo "  make temporal           240 snaps @ 2Hz → 10s MP4 @ 24fps (1 snap = 1 frame)"
	@echo "  make poll-snapshots     OHLCV + snapshot series + series CSV only"
	@echo "  make fetch-ohlcv        price candles only"
	@echo "  make fetch-series       bounded snapshot series only"
	@echo "  make normalize-series   normalize latest series manifest only"
	@echo "  make render-mp4         MP4 from latest bin_atlas_series CSV (needs ffmpeg)"
	@echo "  make simulate-series    synthetic series from real seed (no RPC; for long MP4s)"
	@echo "  make render-mp4-demo    simulate-series + render-mp4 (~60s default)"
	@echo ""
	@echo "Temporal knobs: DATASET, TEMPORAL_COUNT (240), TEMPORAL_POLL_HZ (2),"
	@echo "  TEMPORAL_FPS (24), TEMPORAL_DURATION_SEC (10), SERIES_BINS_LEFT/RIGHT"
	@echo "Poll knobs: OHLCV_TIMEFRAME, OHLCV_LOOKBACK_DAYS,"
	@echo "  FRAME_DURATION, MP4_FPS (for render-mp4), SIM_COUNT, SIM_INTERVAL_SEC,"
	@echo "  SERIES_COUNT, SERIES_RPC_BACKOFF_SEC, SERIES_INTERVAL_SEC"
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

# --- Temporal (make temporal) -----------------------------------------------

# Poll 240 snapshots @ 2 Hz (~2 min), render 1 snap = 1 frame → 10s MP4 at 24 fps.
DATASET ?= alchemy
TEMPORAL_DURATION_SEC ?= 10
TEMPORAL_FPS ?= 24
TEMPORAL_COUNT ?= 240
TEMPORAL_POLL_HZ ?= 2
TEMPORAL_ARGS = --pool $(POOL) --dataset $(DATASET) \
	--duration-sec $(TEMPORAL_DURATION_SEC) --fps $(TEMPORAL_FPS) --count $(TEMPORAL_COUNT) \
	--poll-hz $(TEMPORAL_POLL_HZ)

# --- Snapshot polling pipeline ----------------------------------------------

temporal:
	poetry run python -m meteora_bin_atlas.temporal.run $(TEMPORAL_ARGS) \
		--bins-left $(SERIES_BINS_LEFT) --bins-right $(SERIES_BINS_RIGHT)

# OHLCV + bounded snapshot series + series CSV in one npm script.
poll-snapshots:
	npm run temporal -- $(POLL_SNAPSHOTS_ARGS) --dataset $(DATASET)

# Meteora datapi only; fast (~seconds).
fetch-ohlcv:
	npm run fetch:ohlcv -- $(POOL_ARGS) --timeframe $(OHLCV_TIMEFRAME) --lookback-days $(OHLCV_LOOKBACK_DAYS)

# Repeated bounded bin fetches; slow on public RPC (backoff + interval between snaps).
fetch-series:
	npm run fetch:series -- $(POOL_ARGS) --count $(SERIES_COUNT) --rpc-backoff-sec $(SERIES_RPC_BACKOFF_SEC) --interval-sec $(SERIES_INTERVAL_SEC) --bins-left $(SERIES_BINS_LEFT) --bins-right $(SERIES_BINS_RIGHT)

# Latest snapshot_series manifest → data/processed/bin_atlas_series_<pool>_<ts>.csv
normalize-series:
	npm run normalize:series -- $(POOL_ARGS)

# --- MP4 render (make render-mp4) -------------------------------------------

# Seconds each snapshot stays on screen × fps = frames per snapshot.
# 60 snapshots × 1.0s × 10 fps → ~60s MP4 (use simulate-series first).
FRAME_DURATION ?= 1.0
MP4_FPS ?= 10
RENDER_ARGS = --frame-duration $(FRAME_DURATION) --fps $(MP4_FPS)

# Simulated series from the latest real seed (no RPC). Default 60 snaps ≈ 60s MP4.
SIM_COUNT ?= 60
SIM_INTERVAL_SEC ?= 10
SIM_SEED ?=
SIM_ARGS = --count $(SIM_COUNT) --interval-sec $(SIM_INTERVAL_SEC) $(if $(SIM_SEED),--seed $(SIM_SEED),)

simulate-series:
	poetry run python -m meteora_bin_atlas.temporal.simulate --pool $(POOL) $(SIM_ARGS)

render-mp4:
	poetry run python -m meteora_bin_atlas.temporal.render --pool $(POOL) $(RENDER_ARGS)

render-mp4-demo: simulate-series render-mp4

# --- Notebook ---------------------------------------------------------------

notebook:
	poetry run jupyter notebook notebooks/01_connect_fetch_explore_meteora.ipynb
