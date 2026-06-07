# Meteora Bin Atlas

A small **read-only research sketchbook** for exploring Meteora DLMM bin-level liquidity on Solana.

This is not a trading bot, dashboard, Solana indexer, or production analytics product. It is a compact atlas of Meteora bins: connect via RPC, fetch pool/bin data, normalize snapshots, and visualize liquidity around the active bin.

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
npm install
npm run smoke
```

Set `SOLANA_RPC_URL` in `.env` to a private RPC endpoint for reliable mainnet reads. The smoke script prints only the RPC hostname, not API keys.

### Python / Jupyter

```bash
poetry install
```

Notebook workflow will be added in later steps.

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

## Status

- **Step 0** — TypeScript + Poetry skeleton, Solana RPC smoke test (`npm run smoke`)
- **Step 1** — Domain notes and README (this document)
- **Next** — Pool discovery, bin fetching, normalization, notebook
