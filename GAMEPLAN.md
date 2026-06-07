# GAMEPLAN.md — Meteora Bin Atlas

## Project name

`meteora-bin-atlas`

## Purpose

Build a small, inspectable research sketchbook for understanding Meteora DLMM bin-level liquidity on Solana.

The output should be:

1. A working TypeScript data pipeline that connects to Solana through an RPC endpoint, discovers or selects Meteora DLMM pools, fetches pool/bin data, and writes clean snapshots.
2. A Jupyter notebook that explains how to connect to Solana without running a node, fetch Meteora DLMM data, inspect the bin structure, normalize it, and visualize liquidity around the active bin.
3. A compressed research artifact: clear README, notes, plots, and one reproducible example.

This is not a trading bot, not a dashboard, not a full Solana indexer, and not a production analytics product. It is a small atlas of Meteora bins.

---

## Primary references

### Meteora DLMM docs

* Meteora docs home: https://docs.meteora.ag/
* DLMM developer overview: https://docs.meteora.ag/developer-guide/home
* DLMM concepts: https://docs.meteora.ag/overview/products/dlmm/dlmm-concepts
* DLMM TypeScript SDK functions: https://docs.meteora.ag/developer-guide/guides/dlmm/typescript-sdk/sdk-functions
* Meteora DLMM SDK GitHub: https://github.com/MeteoraAg/dlmm-sdk
* NPM package: https://www.npmjs.com/package/@meteora-ag/dlmm

### Original Liquidity Book / DLMM reference

* Trader Joe / LFJ Liquidity Book whitepaper repo: https://github.com/lfj-gg/LB-Whitepaper
* Whitepaper PDF path: https://github.com/lfj-gg/LB-Whitepaper/blob/main/Joe%20v2%20Liquidity%20Book%20Whitepaper.pdf

### Solana / RPC references

* Solana web3.js docs: https://solana-labs.github.io/solana-web3.js/
* Solana RPC HTTP methods: https://solana.com/docs/rpc/http
* Public RPC is okay for a first test, but this project should support a private RPC URL from Helius, QuickNode, Triton, Alchemy, Chainstack, or another provider.

---

## Non-goals

Do not implement swaps.

Do not require a wallet.

Do not require running a Solana node.

Do not create or manage liquidity positions.

Do not build a frontend unless explicitly requested.

Do not add a database in the first pass.

Do not silently invent fields if Meteora SDK objects differ from expectations.

Do not turn this into a general Solana indexer.

---

## Conceptual frame

Meteora DLMM is a bin-based liquidity system.

For this project, treat each pool as a discrete atlas:

```text
pool
  ├── token X
  ├── token Y
  ├── bin step
  ├── active bin
  └── bin array(s)
        └── bins
              ├── bin id
              ├── price
              ├── token X amount / reserve-like quantity
              ├── token Y amount / reserve-like quantity
              ├── liquidity
              └── distance from active bin
```

Research interpretation:

```text
bin id                = discrete coordinate
active bin            = local origin / current chart center
bin step              = lattice spacing
bin liquidity         = field value on the lattice
liquidity distribution = local market microstructure shape
snapshot              = one time-slice of the field
successive snapshots  = motion/deformation of the field
```

---

## Expected repository structure

```text
meteora-bin-atlas/
  GAMEPLAN.md
  README.md
  package.json
  tsconfig.json
  .env.example
  .gitignore

  src/
    config.ts
    solana.ts
    meteora/
      discoverPools.ts
      fetchPoolState.ts
      fetchBinArrays.ts
      normalizeBins.ts
      types.ts
    io/
      writeJson.ts
      writeCsv.ts
    scripts/
      step0_smoke.ts
      step1_discover_pools.ts
      step2_fetch_pool_snapshot.ts
      step3_fetch_bin_arrays.ts
      step4_normalize_bin_atlas.ts

  notebooks/
    01_connect_fetch_explore_meteora.ipynb

  data/
    raw/
      .gitkeep
    processed/
      .gitkeep

  plots/
    .gitkeep

  notes/
    dlmm_notes.md
    review_log.md
```

---

# Step 0 — Repo foundation and smoke test

## Goal

Create a clean TypeScript + Python/Jupyter project skeleton that can connect to Solana mainnet through an RPC URL and perform a minimal read-only smoke test.

## Cursor instruction

Implement Step 0 only.

Create the repository structure, TypeScript config, package setup, `.env.example`, basic Solana connection helper, and one smoke script.

Do not implement Meteora-specific fetching yet.

## Required dependencies

TypeScript side:

```bash
npm init -y
npm install @solana/web3.js @meteora-ag/dlmm dotenv zod bn.js
npm install -D typescript tsx @types/node
```

Python / notebook side:

```bash
python -m venv .venv
source .venv/bin/activate
pip install jupyter pandas matplotlib pyarrow python-dotenv
```

Optional later:

```bash
pip install plotly
```

## `.env.example`

```bash
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
SOLANA_CLUSTER=mainnet-beta
```

Add note:

```text
For reliable use, replace the public Solana RPC URL with a private RPC endpoint.
This project is read-only and does not require running a Solana node.
```

## `src/config.ts`

Requirements:

* Load `.env`.
* Validate `SOLANA_RPC_URL`.
* Default `SOLANA_CLUSTER` to `mainnet-beta`.
* Export config object.

## `src/solana.ts`

Requirements:

* Export `getConnection()`.
* Use `@solana/web3.js`.
* Set commitment to `"confirmed"`.

## `src/scripts/step0_smoke.ts`

Requirements:

* Connect to RPC.
* Fetch current slot.
* Print:

  * RPC URL host only, not full private URL.
  * cluster
  * current slot
  * success message

## Review checklist

Step 0 is complete when:

* `npm run smoke` works.
* No private RPC key is printed.
* `.env` is ignored by git.
* Cursor has not added trading, wallet, frontend, or database logic.

---

# Step 1 — Domain notes and source capture

## Goal

Create concise notes explaining DLMM bins and why this repo exists.

## Cursor instruction

Implement Step 1 only.

Create `notes/dlmm_notes.md` and update `README.md` with the research purpose, source links, and definitions. Do not write code beyond docs updates.

## `notes/dlmm_notes.md` should include

Sections:

```text
# DLMM notes

## Source links

## What is a bin?

## What is the active bin?

## What is bin step?

## Why bin-level liquidity is microstructure

## How this differs from constant-product AMMs

## Working interpretation for this repo

## Open questions
```

## Important concepts to capture

* DLMM distributes liquidity across discrete bins.
* Each bin represents a fixed price point.
* The active bin is the local point of current exchange.
* Bin step determines spacing between consecutive bins.
* Only a limited first version is needed: fetch, normalize, plot.

## Review checklist

Step 1 is complete when:

* The README states that this is a read-only research sketchbook.
* The notes include links to Meteora docs and Liquidity Book whitepaper.
* The notes avoid making claims not checked against source docs.
* The project still has no wallet or transaction logic.

---

# Step 2 — Pool discovery

## Goal

Fetch or define a small list of candidate Meteora DLMM pools.

## Cursor instruction

Implement Step 2 only.

Add a script to discover or load Meteora DLMM pool addresses. Prefer official Meteora SDK/API routes. If the SDK/API changes or is unclear, make the script robust and document the fallback.

## Implementation options

Option A: Use Meteora SDK static pool discovery if reliable.

Relevant SDK idea:

```ts
DLMM.getLbPairs(connection)
```

Option B: Use Meteora API if documented and reachable.

Known docs example mentions:

```text
https://dlmm-api.meteora.ag/pair/all
```

Option C: Manual seed list.

If discovery fails, create `data/manual_pools.json` with 1–3 known pool addresses, clearly marked as manually supplied.

## Output

Write:

```text
data/raw/pools_<timestamp>.json
data/processed/pool_candidates.csv
```

`pool_candidates.csv` should attempt to include:

```text
pool_address
token_x_mint
token_y_mint
bin_step
active_bin_id
raw_name_or_symbol_if_available
source
fetched_at_utc
```

If a field is unavailable, leave it blank or null. Do not invent symbols.

## Review checklist

Step 2 is complete when:

* The script produces at least one candidate pool or a clear error.
* Any fallback path is documented.
* Raw API/SDK response is saved before normalization.
* No fake token names or addresses are introduced.

---

# Step 3 — Fetch one pool state

## Goal

Given a selected pool address, instantiate a DLMM pool object and fetch basic pool state.

## Cursor instruction

Implement Step 3 only.

Add a script that accepts a pool address and fetches basic state, including active bin if available.

## Required script

```text
src/scripts/step2_fetch_pool_snapshot.ts
```

Command shape:

```bash
npm run fetch:pool -- --pool <POOL_ADDRESS>
```

Accept either:

* CLI argument `--pool`
* or env var `METEORA_POOL_ADDRESS`

## SDK functions to investigate

```ts
DLMM.create(connection, poolPubkey)
dlmmPool.getActiveBin()
dlmmPool.refetchStates()
```

## Output

Write raw and processed files:

```text
data/raw/pool_snapshot_<pool>_<timestamp>.json
data/processed/pool_snapshot_<pool>_<timestamp>.json
```

Processed snapshot should include:

```text
pool_address
active_bin_id
active_bin_price
token_x_mint
token_y_mint
bin_step
fetched_at_utc
```

Only include fields that are genuinely available.

## Review checklist

Step 3 is complete when:

* The script works on one selected pool.
* Active bin information is printed.
* Raw and processed outputs are written.
* Cursor has not added swap or transaction code.

---

# Step 4 — Fetch bin arrays

## Goal

Fetch bin arrays for the selected pool and save them raw.

## Cursor instruction

Implement Step 4 only.

Add a script that fetches bin arrays for one DLMM pool and writes raw JSON. Keep this as close to the SDK representation as possible.

## Required script

```text
src/scripts/step3_fetch_bin_arrays.ts
```

Command shape:

```bash
npm run fetch:bins -- --pool <POOL_ADDRESS>
```

## SDK functions to investigate

```ts
dlmmPool.getBinArrays()
dlmmPool.getBinsAroundActiveBin(...)
dlmmPool.getBinsBetweenLowerAndUpperBound(...)
```

Start with `getBinArrays()` if it is not too large. If too large or slow, use a bounded neighborhood around the active bin.

## Output

```text
data/raw/bin_arrays_<pool>_<timestamp>.json
```

Metadata wrapper:

```json
{
  "pool_address": "...",
  "fetched_at_utc": "...",
  "method": "getBinArrays",
  "raw": []
}
```

## Review checklist

Step 4 is complete when:

* One pool’s bin arrays are written to raw JSON.
* The script does not crash if some BN-like values need serialization.
* The JSON is readable and auditable.
* No normalization is attempted yet beyond safe serialization.

---

# Step 5 — Normalize the bin atlas

## Goal

Convert raw bin arrays into a tidy bin-level table.

## Cursor instruction

Implement Step 5 only.

Add normalization logic that converts raw Meteora bin arrays into a flat table. Make conservative assumptions. If field names differ from expected structure, inspect the raw file and adapt.

## Required files

```text
src/meteora/normalizeBins.ts
src/scripts/step4_normalize_bin_atlas.ts
```

## Target output schema

```text
pool_address
fetched_at_utc
bin_array_index
bin_id
distance_from_active
price
price_per_token
liquidity
x_amount
y_amount
composition_y
is_active_bin
raw_bin_array_pubkey
raw_fields_json
```

Rules:

* `distance_from_active = bin_id - active_bin_id`, if both are available.
* `is_active_bin = true` only when `bin_id === active_bin_id`.
* Keep raw bin fields in `raw_fields_json`.
* If liquidity is unavailable but x/y are available, still output the bin.
* Do not make up decimals.
* If token decimals are needed, create TODOs rather than guessing.

## Outputs

```text
data/processed/bin_atlas_<pool>_<timestamp>.csv
data/processed/bin_atlas_<pool>_<timestamp>.parquet
```

Parquet is optional if annoying. CSV is required.

## Review checklist

Step 5 is complete when:

* A CSV exists with one row per bin.
* Active bin is marked if identifiable.
* The table can be loaded by pandas.
* Missing fields are null, not fabricated.

---

# Step 6 — Jupyter notebook: connect, fetch, explore, visualize

## Goal

Create the main human-facing artifact: a notebook that explains the whole pipeline and visualizes bin liquidity.

## Cursor instruction

Implement Step 6 only.

Create `notebooks/01_connect_fetch_explore_meteora.ipynb`.

The notebook should be readable as a tutorial and research log. It may call TypeScript scripts via shell commands, then load the resulting CSV into pandas.

## Notebook title

```text
Meteora DLMM Bin Atlas: connecting, fetching, normalizing, visualizing
```

## Notebook sections

### 1. Purpose

Explain:

```text
This notebook explores Meteora DLMM bin-level liquidity using a read-only Solana RPC connection. It does not run a Solana node, does not use a wallet, and does not submit transactions.
```

### 2. Environment

Show:

```python
import os
import json
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("../.env")
```

### 3. RPC model

Explain:

```text
We connect through an RPC provider using SOLANA_RPC_URL. This is read-only.
```

### 4. Fetch pool candidates

Use shell command:

```python
!npm run discover:pools
```

Then load latest CSV from `data/processed`.

### 5. Choose one pool

Either:

* select the first candidate, or
* allow manual setting:

```python
POOL_ADDRESS = "..."
```

### 6. Fetch pool snapshot

```python
!npm run fetch:pool -- --pool {POOL_ADDRESS}
```

### 7. Fetch bin arrays

```python
!npm run fetch:bins -- --pool {POOL_ADDRESS}
```

### 8. Normalize bin atlas

```python
!npm run normalize:bins -- --pool {POOL_ADDRESS}
```

### 9. Load bin atlas

```python
df = pd.read_csv(latest_bin_atlas_path)
df.head()
df.info()
```

### 10. Basic sanity checks

Show:

```python
df["bin_id"].min(), df["bin_id"].max()
df["distance_from_active"].min(), df["distance_from_active"].max()
df["is_active_bin"].sum()
```

### 11. Visualize liquidity by bin

Required plot:

```python
plot_df = df.sort_values("bin_id")

plt.figure(figsize=(12, 5))
plt.bar(plot_df["distance_from_active"], plot_df["liquidity"])
plt.axvline(0, linestyle="--")
plt.xlabel("Distance from active bin")
plt.ylabel("Liquidity")
plt.title("Meteora DLMM liquidity by bin")
plt.tight_layout()
plt.show()
```

Do not specify colors unless asked.

### 12. Visualize token composition if available

If x/y amounts exist:

```python
plt.figure(figsize=(12, 5))
plt.plot(plot_df["distance_from_active"], plot_df["x_amount"], label="X amount")
plt.plot(plot_df["distance_from_active"], plot_df["y_amount"], label="Y amount")
plt.axvline(0, linestyle="--")
plt.xlabel("Distance from active bin")
plt.ylabel("Amount")
plt.title("Token composition around active bin")
plt.legend()
plt.tight_layout()
plt.show()
```

### 13. Microstructure notes

Add markdown:

```text
The active bin functions as a local coordinate center. Liquidity away from the active bin is a discrete field over price bins. A single snapshot gives the static shape; repeated snapshots would show migration and deformation.
```

### 14. Next questions

Include:

```text
- How concentrated is liquidity around the active bin?
- How asymmetric is liquidity on either side?
- How does the active bin move over time?
- Do volatile pools show wider liquidity distributions?
- How do stable pools differ from memecoin or SOL pairs?
```

## Review checklist

Step 6 is complete when:

* Notebook runs top to bottom after Step 0–5 scripts work.
* The notebook includes both executable cells and explanatory markdown.
* At least one plot appears.
* The notebook explains that no Solana node is being run.

---

# Step 7 — Add lightweight metrics

## Goal

Compute simple bin-distribution metrics that make the atlas more useful.

## Cursor instruction

Implement Step 7 only.

Add metrics to the notebook and optionally to a TypeScript/Python utility.

## Metrics

Given a bin atlas DataFrame:

```text
number_of_bins
active_bin_id
min_bin_id
max_bin_id
liquidity_total
liquidity_within_5_bins
liquidity_within_10_bins
share_liquidity_within_5_bins
share_liquidity_within_10_bins
left_liquidity
right_liquidity
left_right_imbalance
weighted_mean_distance
weighted_abs_distance
```

Definitions:

```text
left_liquidity = liquidity where distance_from_active < 0
right_liquidity = liquidity where distance_from_active > 0
left_right_imbalance = (right_liquidity - left_liquidity) / total_liquidity
weighted_mean_distance = sum(distance * liquidity) / sum(liquidity)
weighted_abs_distance = sum(abs(distance) * liquidity) / sum(liquidity)
```

Handle zero or missing liquidity safely.

## Output

```text
data/processed/bin_metrics_<pool>_<timestamp>.csv
```

## Review checklist

Step 7 is complete when:

* Metrics table exists.
* Notebook displays metrics.
* No divide-by-zero warnings.
* Metrics are clearly described as exploratory, not trading signals.

---

# Step 8 — Snapshot comparison

## Goal

Allow two snapshots of the same pool to be compared.

## Cursor instruction

Implement Step 8 only.

Add notebook logic for comparing two bin atlas CSV files from the same pool.

## Comparison ideas

```text
delta_liquidity_by_bin
active_bin_change
liquidity_total_change
left_right_imbalance_change
new_bins
removed_bins
```

## Required notebook section

```text
Comparing two snapshots
```

The notebook should detect whether at least two snapshots exist. If not, explain that the user should rerun fetch/normalize later.

## Required plot

Bar chart of `delta_liquidity` by `distance_from_active` or `bin_id`.

## Review checklist

Step 8 is complete when:

* Notebook gracefully handles only one snapshot.
* If two snapshots exist, it can compare them.
* The output makes temporal movement/deformation visually legible.

---

# Step 9 — Compress into a public artifact

## Goal

Turn the work into a small finished object.

## Cursor instruction

Implement Step 9 only.

Update README and create a short artifact note.

## Required files

```text
README.md
notes/artifact_note.md
plots/
```

## README should include

```text
# Meteora Bin Atlas

A small read-only research sketchbook for exploring Meteora DLMM bin-level liquidity on Solana.

## What it does

- Connects to Solana through RPC
- Discovers or selects Meteora DLMM pools
- Fetches pool state and bin arrays
- Normalizes bins into a tidy atlas table
- Visualizes liquidity around the active bin in Jupyter

## What it does not do

- Does not trade
- Does not use a wallet
- Does not submit transactions
- Does not run a Solana node
- Does not make trading recommendations

## Why this matters

DLMM bin liquidity is a discrete microstructure object. Each bin can be interpreted as a coordinate cell; the active bin is the local center; the liquidity distribution is a field over the bin lattice.

## Quickstart

## Sources

## Notebook

## Example plot

## Open questions
```

## `notes/artifact_note.md`

Should be short and publishable:

```text
I built a small Meteora DLMM bin atlas to understand bin-level liquidity as a discrete microstructure object on Solana. The first version connects through RPC, fetches pool/bin data, normalizes the bin array into a tidy table, and visualizes liquidity around the active bin. The purpose is not trading, but research: treating the active bin as a local coordinate center and the liquidity distribution as a field over a price-bin lattice.
```

## Review checklist

Step 9 is complete when:

* README is clear to a stranger.
* Notebook is the main artifact.
* The project can be shown at a Solana hacker event without overclaiming.
* The repo feels finished even if small.

# Step 10 — Read-only swap quotes and slippage curves

## Goal

Add slippage computation as a final research layer.

This step should estimate how output changes as input size increases, using Meteora DLMM quote functions or a conservative bin-walking approximation. It should remain read-only.

This is not a trading bot.

This step should not submit transactions, create swap instructions, use a wallet, hold private keys, or recommend trades.

---

## Conceptual purpose

The bin atlas describes the static liquidity field.

Slippage analysis asks:

```text id="23stn2"
If a hypothetical swap consumes this local liquidity field,
how does execution quality degrade as size increases?
```

Interpretation:

```text id="kthj9j"
small input amount  → mostly local / active-bin execution
large input amount  → crosses more bins
crossed bins        → discrete path through the liquidity lattice
slippage curve      → observed execution degradation as a function of size
```

The research object is the curve:

```text id="eaat41"
input size → expected output → execution price → price impact / slippage
```

---

## Important distinction

There are two different quantities:

### 1. User slippage tolerance

This is the tolerance a user gives to protect against worse-than-quoted execution between quote time and transaction execution.

Example:

```text id="s8qm1y"
quote output = 100.0 token Y
slippage tolerance = 0.5%
minimum acceptable output = 99.5 token Y
```

### 2. Price impact / computed slippage

This is the degradation caused by consuming liquidity across the DLMM bins.

This project is interested in computed slippage / price impact as a microstructure measurement.

Do not confuse these.

---

## Cursor instruction

Implement Step 10 only.

Add read-only quote and slippage analysis. Prefer the official Meteora DLMM SDK quote method if available and reliable. Do not implement real swaps. Do not create transactions. Do not add wallet logic.

If the SDK quote function is hard to use, document why and implement a conservative offline placeholder that uses saved bin atlas data, clearly marked as experimental.

---

## SDK functions to investigate

Start by inspecting the installed `@meteora-ag/dlmm` package and current Meteora docs.

Potentially relevant functions:

```ts id="kl1wpd"
dlmmPool.swapQuote(...)
dlmmPool.getBinArrayForSwap(...)
```

The exact signature may differ by SDK version, so do not assume blindly. Inspect the TypeScript definitions in `node_modules/@meteora-ag/dlmm`.

Search locally:

```bash id="fef6jw"
grep -R "swapQuote" node_modules/@meteora-ag/dlmm -n | head -20
grep -R "getBinArrayForSwap" node_modules/@meteora-ag/dlmm -n | head -20
```

Then check the SDK types before implementation.

---

## Required files

```text id="ng2n5f"
src/meteora/quoteSlippage.ts
src/scripts/step10_quote_slippage.ts
notebooks/01_connect_fetch_explore_meteora.ipynb
notes/slippage_notes.md
```

Optional:

```text id="d6uq34"
data/processed/slippage_quotes_<pool>_<timestamp>.csv
plots/slippage_curve_<pool>_<timestamp>.png
```

---

## Command shape

```bash id="n0dbl7"
npm run quote:slippage -- --pool <POOL_ADDRESS> --mint-in <TOKEN_MINT> --direction yToX --amounts 0.1,0.5,1,2,5,10
```

or:

```bash id="aubkyt"
npm run quote:slippage -- --pool <POOL_ADDRESS> --token-in X --amounts 0.1,0.5,1,2,5,10
```

Use whichever is less fragile after inspecting SDK types.

---

## Output schema

Write:

```text id="dakjda"
data/processed/slippage_quotes_<pool>_<timestamp>.csv
```

Columns:

```text id="cv50kd"
pool_address
fetched_at_utc
direction
token_in_mint
token_out_mint
amount_in_ui
amount_in_raw
expected_amount_out_raw
expected_amount_out_ui
execution_price
reference_price
price_impact_pct
computed_slippage_pct
min_out_at_10bps
min_out_at_50bps
min_out_at_100bps
bins_crossed
quote_method
raw_quote_json
```

Rules:

* `amount_in_ui` is human-readable token amount.
* `amount_in_raw` is integer base-unit amount.
* Do not guess token decimals. Fetch decimals from mint accounts or require manual decimals.
* `execution_price = expected_amount_out_ui / amount_in_ui`, adjusted for direction.
* `reference_price` should be the active bin price if available.
* `price_impact_pct` should compare execution price against active/reference price.
* `computed_slippage_pct` may initially equal `price_impact_pct`, but document the convention.
* `min_out_at_10bps`, `min_out_at_50bps`, `min_out_at_100bps` are tolerance-adjusted minimum outputs, not computed market slippage.
* `bins_crossed` should be populated only if the SDK exposes this or if reliably inferred. Otherwise null.
* `raw_quote_json` should preserve the quote object.

---

## Slippage formulas

Use basis points for tolerance:

```text id="0wwivn"
1 bp = 0.01%
10 bps = 0.10%
50 bps = 0.50%
100 bps = 1.00%
```

Minimum output under tolerance:

```text id="1bi4bq"
min_out = expected_out * (1 - tolerance_bps / 10000)
```

Execution price:

```text id="ktyy79"
execution_price = amount_out / amount_in
```

Price impact convention:

```text id="wm901f"
price_impact_pct = 100 * (reference_price - execution_price) / reference_price
```

If direction or price convention makes this sign wrong, fix it explicitly and document the convention in `notes/slippage_notes.md`.

---

## Notebook additions

Add a final notebook section:

```text id="t8agvj"
## Read-only slippage analysis
```

Subsections:

```text id="unnoz0"
### What slippage means here
### Generate quote sizes
### Fetch read-only quotes
### Load slippage quote table
### Plot input size vs expected output
### Plot input size vs computed slippage / price impact
### Interpret the curve
```

Required notebook plot 1:

```python id="d3t7bp"
quotes = quotes.sort_values("amount_in_ui")

plt.figure(figsize=(10, 5))
plt.plot(quotes["amount_in_ui"], quotes["expected_amount_out_ui"], marker="o")
plt.xlabel("Input amount")
plt.ylabel("Expected output amount")
plt.title("Expected output by input size")
plt.tight_layout()
plt.show()
```

Required notebook plot 2:

```python id="g5d06n"
plt.figure(figsize=(10, 5))
plt.plot(quotes["amount_in_ui"], quotes["price_impact_pct"], marker="o")
plt.xlabel("Input amount")
plt.ylabel("Price impact (%)")
plt.title("Read-only quoted price impact by input size")
plt.tight_layout()
plt.show()
```

Do not specify colors.

---

## `notes/slippage_notes.md`

Create a concise note:

```text id="lcr9ao"
# Slippage notes

## What this project computes

This project computes read-only quote-based price impact across hypothetical input sizes.

## What this project does not compute

It does not execute swaps.
It does not model transaction inclusion.
It does not model MEV, latency, priority fees, or failed transactions.
It does not recommend trades.

## Slippage tolerance vs price impact

Slippage tolerance is a user protection parameter.
Price impact is the deterioration in execution price caused by consuming liquidity.

## DLMM interpretation

A swap consumes liquidity through one or more bins. Small trades may remain near the active bin. Larger trades may cross bins, creating a piecewise/discrete slippage curve.

## Open questions

- How many bins are crossed for each trade size?
- Does the curve have visible kinks at bin boundaries?
- How asymmetric are X-to-Y versus Y-to-X quotes?
- How does slippage change over time as liquidity migrates?
```

---

## Review checklist

Step 10 is complete when:

* The project can produce a slippage quote CSV for one selected pool.
* The notebook plots expected output and price impact against input size.
* The code remains read-only.
* There is no wallet, no private key, no transaction signing, and no swap submission.
* Token decimals are handled honestly.
* Any SDK uncertainty is documented.
* The analysis distinguishes slippage tolerance from quote-based price impact.

---

# Step 11 — Optional slippage surface over time

## Goal

Compare slippage curves across multiple snapshots or quote times.

This is optional. Do not do this until Step 10 is stable.

## Cursor instruction

Implement Step 11 only if requested.

Add a time comparison for slippage curves by running Step 10 more than once for the same pool.

## Output

```text id="a4zhbm"
data/processed/slippage_surface_<pool>.csv
```

Columns:

```text id="q8ebsq"
pool_address
quote_time_utc
direction
amount_in_ui
expected_amount_out_ui
price_impact_pct
active_bin_id
reference_price
```

## Notebook plot

```python id="0mcn0g"
for quote_time, group in surface.groupby("quote_time_utc"):
    group = group.sort_values("amount_in_ui")
    plt.plot(group["amount_in_ui"], group["price_impact_pct"], marker="o", label=quote_time)

plt.xlabel("Input amount")
plt.ylabel("Price impact (%)")
plt.title("Slippage curve over time")
plt.legend()
plt.tight_layout()
plt.show()
```

## Review checklist

Step 11 is complete when:

* Multiple quote times can be compared.
* The notebook shows how the slippage curve changes over time.
* The analysis is still read-only and non-trading.

---

# Cursor operating protocol

Use one step at a time.

For each step:

1. Implement only the current step.
2. Keep changes minimal.
3. Run the relevant script.
4. Report:

   * files changed
   * command run
   * output summary
   * known issues
5. Stop for human review.

Do not proceed to the next step until explicitly asked.

---

# Review style for human

After each Cursor step, review these questions:

```text
Did it keep the scope small?
Did it avoid fake fields?
Did it save raw data before processing?
Did it preserve a clean path to the notebook?
Did it avoid transaction/wallet/trading code?
Did it make the next step easier?
```

---

# First Cursor prompt

Use this exact prompt first:

```text
Read GAMEPLAN.md. Implement Step 0 only.

Create the TypeScript + Jupyter repo foundation for a read-only Meteora DLMM bin atlas. Add package setup, tsconfig, .env.example, .gitignore, config loading, a Solana connection helper, and a smoke script that connects to the RPC endpoint and prints the current slot.

Do not implement Meteora fetching yet. Do not add wallet, transaction, swap, frontend, or database code.

After implementation, show files changed, commands to run, and any assumptions.
```

---

# Second Cursor prompt

After reviewing Step 0, use:

```text
Read GAMEPLAN.md. Implement Step 1 only.

Create concise domain notes and update the README. Include source links for Meteora DLMM docs, SDK docs, GitHub SDK, and the Trader Joe / LFJ Liquidity Book whitepaper. Explain bins, active bin, bin step, and the read-only purpose of this repo.

Do not add new code beyond docs updates.
```

---

# Third Cursor prompt

After reviewing Step 1, use:

```text
Read GAMEPLAN.md. Implement Step 2 only.

Add pool discovery. Prefer official Meteora SDK/API routes. Save raw responses and a processed pool_candidates.csv. If discovery fails, document the failure and create a clean manual_pools.json fallback path.

Do not fetch bin arrays yet. Do not add wallet, transaction, swap, frontend, or database code.
```
