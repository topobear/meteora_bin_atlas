# GAMEPLAN.md - Meteora Geometry Broadcast Artifacts

## Project name

`meteora-bin-atlas`

## Purpose

Build a cron-style artifact factory scoped to this.

Meteora DLMM remains the first subject because it exposes real AMM geometry: discrete price bins, active-bin drift, token composition, reserve envelopes, and liquidity terrain. The repo should no longer be framed as only a cool visualization or a private research sketchbook. Each run should produce a compact public artifact that compounds into an archive of market-structure geometry over time.

The canonical output is a post-ready public drop:

1. A cinematic MP4 suitable for X.
2. A poster frame / thumbnail.
3. A short caption with a compact scientific reading.
4. A machine-readable run manifest.
5. An append-only archive index.
6. Links to the source snapshot series and metrics used to make the visual.

It should run a few times per day from cron or a manual operator command, avoiding always-on infra and minimizing exposure to RPC fragility.

---

## Current repo state

The repo already has a working foundation:

| Area | Status |
| --- | --- |
| TypeScript Solana/Meteora fetch pipeline | Present |
| Pool discovery and manual pool seed | Present |
| Pool state fetch | Present |
| Bin-array fetch and normalization | Present |
| Temporal snapshot polling | Present |
| OHLCV fetch from Meteora datapi | Present |
| `bin_atlas_series_*.csv` normalization | Present |
| 2D seismic MP4 renderer | Present |
| Reserve-space companion MP4 renderer | Present |
| 3D spatiotemporal MP4 renderer | Present |
| Timelapse pipeline | Present |
| Simulated series fallback | Present |
| Jupyter exploration notebook | Present |

Known mismatch to fix during cleanup:

* `README.md`, `Makefile`, and `package.json` reference a currency-triangle command, but `src/scripts/step9_fetch_currency_triangle.ts` is missing in the current tree. Either restore that script or remove the references until it is implemented again.

---

## Venture frame

The public artifact should read as **cyber-scientific market fieldwork**:

```text
DLMM pool       = observable market geometry
bin id          = discrete price coordinate
active bin      = current operating point
liquidity       = depth field on the lattice
token mix       = local inventory state
snapshot series = field motion through time
render          = public instrument readout
archive         = compounding atlas of crypto geometry
```

The output should feel closer to a TouchDesigner / scientific-instrument visual than a dashboard. The goal is not just to show liquidity, but to create a repeatable visual language for AMM geometry that can become recognizable across posts.

---

## Default public-drop profile

Default subject:

```text
Pool: SOL-USDC
Address: 5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6
Protocol: Meteora DLMM
```

Default cadence:

```text
3 runs per day
short artifacts
operator-review before posting
no automatic X publishing in v1
```

Default run shape:

```text
Renderer: spatiotemporal primary
Companions: seismic and reserve-space optional
Duration: 10 seconds
FPS: 24
Snapshots: 240
Poll rate: 1.5 Hz when private RPC is available
Bins: wider 3D context, around +/-70 bins by default
```

Dataset priority:

1. `DATASET=alchemy` or another private endpoint through `SOLANA_RPC_URL`.
2. `DATASET=solana-public` for slower fallback runs.
3. `DATASET=simulated` for visual QA only, never labeled as live market data.

---

## Non-goals

Do not implement swaps.

Do not require a wallet.

Do not submit transactions.

Do not create or manage liquidity positions.

Do not run a Solana node.

Do not build a 24/7 service for v1.

Do not depend on fragile always-on QuickNode-style infra.

Do not automatically post to X in v1.

Do not make trading recommendations.

Do not silently present simulated data as live data.

---

## Public artifact contract

Each successful public drop should create one directory:

```text
artifacts/
  drops/
    YYYY-MM-DD/
      <timestamp_slug>/
        video.mp4
        poster.png
        caption.md
        manifest.json
        metrics.json
        sources/
          source_paths.json
        companions/
          seismic.mp4        # optional
          reserve.mp4        # optional
  index.jsonl
```

`video.mp4` is the primary X-ready visual.

`poster.png` is a still frame suitable for previewing, threading, or archiving.

`caption.md` is the human-facing post copy. It should be concise and should not include trading advice.

`manifest.json` is the provenance record for the run.

`metrics.json` is the compact scientific readout used by the caption and later archive pages.

`artifacts/index.jsonl` is append-only. Each line points to one public drop.

---

## Manifest schema

The first implementation can use a plain JSON object with these fields:

```json
{
  "run_id": "2026-06-15T04-00-00Z_sol-usdc_spatiotemporal",
  "created_at_utc": "2026-06-15T04:00:00Z",
  "profile": "public-drop-v1",
  "pool_address": "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6",
  "pool_label": "SOL-USDC",
  "protocol": "Meteora DLMM",
  "dataset": "alchemy",
  "renderer": "spatiotemporal",
  "snapshot_count": 240,
  "fps": 24,
  "duration_sec": 10,
  "poll_hz": 1.5,
  "bins_left": 70,
  "bins_right": 70,
  "source_series_csv": "data/processed/bin_atlas_series_...",
  "source_manifest_json": "data/processed/snapshot_series_...",
  "outputs": {
    "video": "artifacts/drops/.../video.mp4",
    "poster": "artifacts/drops/.../poster.png",
    "caption": "artifacts/drops/.../caption.md",
    "metrics": "artifacts/drops/.../metrics.json"
  },
  "summary": {
    "active_bin_start": 0,
    "active_bin_end": 0,
    "active_bin_delta": 0,
    "price_start": 0,
    "price_end": 0,
    "price_delta_pct": 0,
    "liquidity_peak": 0,
    "liquidity_total_start": 0,
    "liquidity_total_end": 0
  }
}
```

Keep this schema intentionally small. Add fields only when they are used by a renderer, caption, archive page, or QA check.

---

## Caption rules

The caption should be generated into `caption.md`, not posted automatically.

Requirements:

* Under X's post length limit.
* Include pool label and protocol.
* Identify the artifact as live or simulated.
* Include one or two metric facts from `metrics.json`.
* Use a consistent phrase for the venture, e.g. `continuous cinematic broadcasting of crypto geometry`.
* Include a short non-advice disclaimer.
* Do not mention private RPC hosts or API providers.
* Do not include price predictions.

Example shape:

```text
Meteora SOL-USDC geometry, sampled live.

240 DLMM snapshots compressed into 10s: active bin drifted +3 bins while liquidity stayed clustered around the operating point.

continuous cinematic broadcasting of crypto geometry

Not trading advice.
```

---

## Command targets

Add a canonical operator command:

```bash
make public-drop
```

Expected behavior:

1. Fetch a live snapshot series for the configured pool.
2. Normalize the series.
3. Render the primary MP4.
4. Extract a poster frame.
5. Compute compact run metrics.
6. Write `caption.md`.
7. Write `manifest.json`.
8. Append one line to `artifacts/index.jsonl`.

Useful overrides:

```bash
make public-drop DATASET=solana-public
make public-drop DATASET=simulated
make public-drop POOL=<address>
make public-drop RENDERER=spatiotemporal
make public-drop RENDERER=seismic
make public-drop COMPANIONS=1
```

The command should fail clearly if live RPC configuration is missing and `DATASET` is not `solana-public` or `simulated`.

---

## Cron profile

The recommended v1 cron shape is three operator-reviewable drops per day.

Example crontab shape:

```cron
0 8,14,20 * * * cd /path/to/meteora-bin-atlas && make public-drop >> logs/public-drop.log 2>&1
```

Implementation notes:

* Log to `logs/public-drop.log`.
* Do not delete prior artifacts automatically.
* If a run fails, it should not append to `artifacts/index.jsonl`.
* A partial drop directory may remain for debugging, but the manifest should mark failure only if failure manifests are deliberately implemented.
* V1 does not need retries beyond the existing pipeline's RPC handling.

---

## Step 7 - Artifact metrics and manifest

### Goal

Create the small scientific readout that turns a render into a public artifact with provenance.

### Required behavior

Implement a Python module that loads a `bin_atlas_series_*.csv` and computes:

* Snapshot count.
* First and last timestamps.
* Active-bin start, end, min, max, and delta.
* Price start, end, and percent change.
* Total liquidity start and end.
* Peak liquidity.
* Approximate concentration around the active bin.
* Token composition summary where available.

Write:

```text
metrics.json
manifest.json
```

### Review checklist

Step 7 is complete when:

* Metrics can be generated from the latest real series.
* Metrics can be generated from a simulated series.
* Manifest paths point to files that exist.
* Failed runs do not produce an index entry.

---

## Step 8 - Poster frame and caption generator

### Goal

Make each render post-ready without manual screenshotting or caption drafting.

### Required behavior

Add poster extraction:

* Default to a frame around 60 percent through the MP4.
* Use `ffmpeg` if available.
* Output `poster.png`.
* Fail clearly if no MP4 exists.

Add caption generation:

* Read `manifest.json` and `metrics.json`.
* Write `caption.md`.
* Keep copy compact, factual, and non-advisory.
* Clearly label simulated runs.

### Review checklist

Step 8 is complete when:

* `poster.png` is generated for spatiotemporal, seismic, and timelapse MP4s.
* `caption.md` is generated without hand editing.
* Captions stay inside X length limits.
* Captions do not make trading claims.

---

## Step 9 - `make public-drop`

### Goal

Create one command that makes a complete public artifact directory.

### Required behavior

Add a Python orchestration module and Make target that:

1. Resolves pool, dataset, renderer, cadence, and output directory.
2. Runs the existing temporal fetch path.
3. Runs the selected renderer.
4. Moves or writes output media into the drop directory.
5. Generates poster, metrics, caption, and manifest.
6. Appends the manifest summary to `artifacts/index.jsonl`.

Default renderer:

```text
spatiotemporal
```

Supported renderer options:

```text
spatiotemporal
seismic
timelapse
```

V1 can call existing Python functions directly. It does not need a new database or service.

### Review checklist

Step 9 is complete when:

* `make public-drop DATASET=simulated` completes without RPC.
* `make public-drop DATASET=solana-public` works on a healthy public RPC.
* `make public-drop` works when `SOLANA_RPC_URL` is set.
* A complete drop directory is created.
* `artifacts/index.jsonl` receives exactly one valid JSON line per successful run.

---

## Step 10 - Archive index and gallery readiness

### Goal

Make the public drops compound into a durable archive, not a folder of disconnected videos.

### Required behavior

Keep `artifacts/index.jsonl` append-only and easy to parse.

Add a small archive summary command:

```bash
make artifact-index
```

The summary should report:

* Number of drops.
* Date range.
* Pools covered.
* Renderers used.
* Latest drop path.
* Missing media or manifest paths, if any.

Optional v1 addition:

* Generate `artifacts/index.md` as a human-readable local gallery index.

### Review checklist

Step 10 is complete when:

* The archive index can be rebuilt or validated.
* Missing files are reported.
* The index can support a future static gallery without changing prior manifests.

---

## Step 11 - Currency triangle cleanup or restoration

### Goal

Resolve the current repo mismatch around the currency-triangle feature.

### Required behavior

Choose one:

1. Restore `src/scripts/step9_fetch_currency_triangle.ts` and make `make currency-triangle` work again.
2. Remove the stale `currency-triangle` command references from `README.md`, `Makefile`, and `package.json`.

If restored, keep it read-only and separate from `public-drop` v1. The triangle can become a later artifact family, but it should not block the Meteora DLMM visual drop pipeline.

### Visual direction

Evolve the currency triangle composite (`triangle_temporal_*.mp4` — three leg seismic strips on a SOL/USDC/USDT lattice) into a **miniature scientific instrument** readout: a compact, self-contained viz that reads like a dedicated three-asset geometry probe, not three pool views stitched together. Same cyber-scientific fieldwork language as the main drops — instrument casing, calibrated scales, leg labels as probe channels, the triangle as the coordinate frame for cross-rate geometry.

The payoff is **repeatable visual measurement**: fixed scales, consistent framing, and leg geometry that stay stable across runs so you can read the same instrument before, during, and after market events (depegs, volatility spikes, routing stress) and compare frames like instrument traces rather than re-interpreting a new chart each time.

### Review checklist

Step 11 is complete when:

* `npm run fetch:triangle` either works or no longer exists.
* README no longer advertises a missing command.
* The public-drop pipeline is not coupled to Jupiter or triangle routing.

---

## Acceptance criteria

The artifact-factory milestone is complete when:

```bash
make public-drop DATASET=simulated
```

creates:

```text
artifacts/drops/<date>/<run_id>/video.mp4
artifacts/drops/<date>/<run_id>/poster.png
artifacts/drops/<date>/<run_id>/caption.md
artifacts/drops/<date>/<run_id>/manifest.json
artifacts/drops/<date>/<run_id>/metrics.json
artifacts/index.jsonl
```

And:

* The manifest references existing files.
* The caption is post-ready.
* The run is clearly labeled simulated or live.
* The visual pipeline can still run the existing temporal/spatiotemporal targets.
* No wallet, trading, transaction, or 24/7 service logic has been added.

---

## First implementation prompt

```text
Read GAMEPLAN.md and current repo state.
Implement Step 7 only: artifact metrics and manifest generation.
Do not implement public-drop orchestration yet.
Use simulated and latest real bin_atlas_series CSVs as test inputs.
```

## Second implementation prompt

```text
Read GAMEPLAN.md.
Implement Step 8 only: poster frame extraction and caption generation.
Do not add cron or X posting.
```

## Third implementation prompt

```text
Read GAMEPLAN.md.
Implement Step 9 only: make public-drop orchestration using existing renderers.
Default to spatiotemporal.
Ensure DATASET=simulated works without RPC.
```
