# DLMM notes

Concise working notes for this repo. Claims below are grounded in Meteora docs and the original Liquidity Book reference unless marked as open questions.

## Source links

### Meteora DLMM

- [Meteora docs home](https://docs.meteora.ag/)
- [What is DLMM?](https://docs.meteora.ag/core-products/dlmm/what-is-dlmm.md)
- [DLMM formulas](https://docs.meteora.ag/core-products/dlmm/formulas.md)
- [DLMM developer guide](https://docs.meteora.ag/developer-guides/dlmm/index.md)
- [DLMM TypeScript SDK functions](https://docs.meteora.ag/developer-guides/dlmm/typescript-sdk/sdk-functions.md)
- [Meteora DLMM SDK (GitHub)](https://github.com/MeteoraAg/dlmm-sdk)
- [@meteora-ag/dlmm (npm)](https://www.npmjs.com/package/@meteora-ag/dlmm)

### Original Liquidity Book / DLMM lineage

- [LFJ Liquidity Book whitepaper repo](https://github.com/lfj-gg/LB-Whitepaper)
- [Joe v2 Liquidity Book whitepaper (PDF)](https://github.com/lfj-gg/LB-Whitepaper/blob/main/Joe%20v2%20Liquidity%20Book%20Whitepaper.pdf)

### Solana / RPC (read-only access)

- [Solana web3.js](https://solana-labs.github.io/solana-web3.js/)
- [Solana RPC HTTP methods](https://solana.com/docs/rpc/http)

## What is a bin?

In Meteora DLMM, a pool is a ladder of discrete **price bins**. Each bin represents one fixed price point for a token pair.

Per [Meteora's DLMM overview](https://docs.meteora.ag/core-products/dlmm/what-is-dlmm.md):

- Liquidity is organized into bins placed side by side, not spread across every possible price like a traditional full-range AMM.
- Trades **inside** a bin execute at that bin's fixed price (zero price impact within the bin).
- Price moves when liquidity in the active bin is consumed and the pool shifts to the next bin.
- Bins are grouped into bin arrays of 70 bins on-chain.

Each bin holds token reserves for its price level. In formula terms ([DLMM formulas](https://docs.meteora.ag/core-products/dlmm/formulas.md)), bin liquidity relates token amounts at price `P`: `L = P · x + y`.

## What is the active bin?

There is **only one active bin at a time** in a DLMM pool.

Per Meteora docs, the active bin is where the current market price sits and where swaps are happening now. The pool stores an active bin ID on the `LbPair` account.

Bins below the active bin hold one side of the pair; bins above hold the other. As swaps consume liquidity from the active bin, the pool can move left or right through the bin ladder.

For this repo, the active bin is the natural **local origin** when sampling liquidity around the current price.

## What is bin step?

**Bin step** is the configured spacing between neighboring bins, measured in **basis points** (10,000 bps = 100%).

Per [DLMM formulas](https://docs.meteora.ag/core-products/dlmm/formulas.md), bin price follows:

```text
P_i = (1 + bin_step / 10,000)^i
```

where `i` is the bin ID. Example from Meteora docs: a bin step of 25 bps means neighboring bins are roughly 0.25% apart.

Smaller bin steps create tighter price movement (often used for stable or highly liquid pairs). Larger bin steps create wider jumps (often used for volatile assets). Meteora docs note the program supports bin steps up to 400 bps.

## Why bin-level liquidity is microstructure

Constant-product AMMs expose a smooth reserve curve. DLMM exposes a **discrete lattice** of price cells, each with its own reserves and liquidity.

That makes local market structure visible:

- Where liquidity is concentrated relative to the active bin
- How depth is distributed across price levels
- How the shape changes over time as LPs rebalance and trades move the active bin

Bin-level data is therefore closer to **market microstructure** than a single pool-level reserve ratio. This repo treats each snapshot as a field over bin coordinates, not as a single "price + TVL" summary.

## How this differs from constant-product AMMs

| Aspect | Constant-product AMM (e.g. x·y = k) | DLMM |
|--------|-------------------------------------|------|
| Price representation | Continuous curve across all prices | Discrete bins at fixed prices |
| Liquidity placement | Typically full-range or broad band | Concentrated into selected bin ranges |
| Trade within one "step" | Price moves continuously with size | Zero price impact within one bin; price jumps when active bin is consumed |
| LP strategy | Simpler passive curve | Shapes (Spot, Curve, Bid-Ask), resizable ranges, dynamic fees |
| What this repo studies | Not the focus | Bin lattice around the active bin |

Meteora's DAMM products are separate from DLMM; this repo focuses on DLMM bin structure only.

## Working interpretation for this repo

This project is a **read-only research sketchbook**. It does not trade, use a wallet, or submit transactions.

Planned first version (fetch → normalize → plot):

```text
pool
  ├── token X / token Y
  ├── bin step
  ├── active bin id
  └── bin arrays
        └── bins
              ├── bin id
              ├── price
              ├── token amounts / reserves
              ├── liquidity (as exposed by SDK or on-chain decode)
              └── distance from active bin
```

Research mapping:

- **Bin id** → discrete coordinate on the price lattice
- **Active bin** → local origin / chart center
- **Bin step** → lattice spacing
- **Liquidity per bin** → field value at each cell
- **Snapshot** → one time slice of that field
- **Successive snapshots** → how the field deforms over time

Scope guardrails: no swaps, no wallet, no frontend, no database in the first pass. Inspect SDK/on-chain fields rather than inventing schema.

## Open questions

- Which Meteora SDK fields best map to "reserve-like" token X/Y amounts vs. liquidity shares for normalized output?
- For a given pool, how many bin arrays must be fetched to cover a useful neighborhood around the active bin?
- How stable are Meteora API routes (`dlmm-api.meteora.ag`) vs. SDK-only discovery for pool metadata?
- What is the practical RPC cost/latency tradeoff for repeated bin-array reads on mainnet?
- Should normalized output prefer human prices or raw Q64.64 fixed-point values from on-chain data?

These will be resolved during implementation steps, with raw data saved before normalization.
