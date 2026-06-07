.PHONY: help install install-ts install-py smoke discover fetch-pool fetch-bins normalize-bins \
	fetch-ohlcv fetch-series normalize-series temporal atlas notebook

# Default pool: SOL-USDC from data/manual_pools.json
POOL ?= 5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6

# Single-snapshot bin fetch
BINS_LEFT ?= 30
BINS_RIGHT ?= 30
BOUNDED ?=

# Temporal: OHLCV
OHLCV_TIMEFRAME ?= 1h
OHLCV_LOOKBACK_DAYS ?= 7

# Temporal: live snapshot series
SERIES_COUNT ?= 20
SERIES_INTERVAL_SEC ?= 30
SERIES_BINS_LEFT ?= 30
SERIES_BINS_RIGHT ?= 30

POOL_ARGS = --pool $(POOL)
BINS_BOUNDED_ARGS = $(if $(BOUNDED),--bounded --bins-left $(BINS_LEFT) --bins-right $(BINS_RIGHT),)

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
	@echo "  make fetch-ohlcv        price candles (OHLCV_TIMEFRAME, OHLCV_LOOKBACK_DAYS)"
	@echo "  make fetch-series       bounded snapshot series (SERIES_COUNT, SERIES_INTERVAL_SEC)"
	@echo "  make normalize-series   combined bin_atlas_series CSV"
	@echo "  make temporal           fetch-ohlcv + fetch-series + normalize-series"
	@echo ""
	@echo "Notebook"
	@echo "  make notebook         launch Jupyter notebook"
	@echo ""
	@echo "Override pool: make fetch-pool POOL=<address>"

install: install-ts install-py

install-ts:
	npm install

install-py:
	poetry install

smoke:
	npm run smoke

discover:
	npm run discover:pools

fetch-pool:
	npm run fetch:pool -- $(POOL_ARGS)

fetch-bins:
	npm run fetch:bins -- $(POOL_ARGS) $(BINS_BOUNDED_ARGS)

normalize-bins:
	npm run normalize:bins -- $(POOL_ARGS)

atlas: discover fetch-pool fetch-bins normalize-bins

fetch-ohlcv:
	npm run fetch:ohlcv -- $(POOL_ARGS) --timeframe $(OHLCV_TIMEFRAME) --lookback-days $(OHLCV_LOOKBACK_DAYS)

fetch-series:
	npm run fetch:series -- $(POOL_ARGS) --count $(SERIES_COUNT) --interval-sec $(SERIES_INTERVAL_SEC) --bins-left $(SERIES_BINS_LEFT) --bins-right $(SERIES_BINS_RIGHT)

normalize-series:
	npm run normalize:series -- $(POOL_ARGS)

temporal: fetch-ohlcv fetch-series normalize-series

notebook:
	poetry run jupyter notebook notebooks/01_connect_fetch_explore_meteora.ipynb
