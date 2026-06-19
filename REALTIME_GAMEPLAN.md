# REALTIME_GAMEPLAN.md - Ubuntu DLMM Triangle Recorder

## Goal

Build the minimum read-only realtime recorder that can produce dense Meteora DLMM
triangle data for high-frequency MP4 artifacts.

The target artifact is:

```text
30 minutes of market activity -> 30 minutes of video
24 fps MP4
43,200 frames per leg
3 Meteora DLMM pools
same bin_atlas_series_*.csv schema the current renderer already consumes
```

This is not a trading system. It never uses a wallet, never submits
transactions, and never manages positions. It is a market-geometry camera.

## Reality Check

This plan is free-only.

Allowed sources:

- Alchemy free tier.
- Helius free tier, only if it works without adding a paid plan.
- Solana public RPC, only for low-rate fallback and smoke tests.
- Meteora public APIs, for pool metadata/OHLCV/context only.
- Locally recorded account history on the Ubuntu machine.

Not allowed:

- Paid Geyser.
- Paid LaserStream.
- Paid archive/replay subscriptions.
- Any service that requires entering billing details to run the artifact job.

Alchemy is useful for current reads and forward recording, but it should not be
treated as a historical bin-state backfill product. Helius free tier may be
worth testing for enhanced WebSockets or LaserStream access, but the plan must
not depend on paid replay. If a free provider removes access or throttles too
hard, fall back to lower-frequency cron captures rather than upgrading spend.

Current repo polling through `getBinsAroundActiveBin(30,30)` is already the
right RPC fallback, but live RPC polling cannot hit the visual target:

```text
24 fps x 3 legs = 72 interleaved snapshots/sec
Alchemy observed sustainable bounded polling ~= 1.5-2 interleaved snapshots/sec
```

So the realtime plan is:

1. Use RPC once to discover the accounts to watch.
2. Subscribe to DLMM pool and bin-array account changes.
3. Persist account updates as they arrive.
4. Materialize fixed-rate 24 fps frame CSVs later with hold-last-observation.
5. Feed those CSVs into the existing triangle renderer.

If Alchemy WebSocket account subscriptions are reliable enough on the Ubuntu
machine, use Alchemy first. If Alchemy is not reliable, try Helius free tier
second. If neither free WebSocket path is reliable, use the existing bounded
RPC polling path as the artifact source and accept lower temporal density.

Historical lag is allowed, but free-only changes what "historical" means:

```text
Best free historical data = the history this Ubuntu recorder has already saved.
```

For example, run the recorder continuously or several times per day, then render
yesterday's best 30 minute window from local files. Do not expect a free provider
to give exact historical DLMM bin-array account state for arbitrary past windows.

## Existing Repo Seams

Use the existing pipeline contracts instead of rewriting render code.

Important files:

```text
src/meteora/fetchTriangleSeries.ts
  Current interleaved bounded polling path.

src/meteora/normalizeBins.ts
  Canonical bin_atlas row schema and current raw -> row normalization.

src/meteora/normalizeSnapshotSeries.ts
  Combines raw snapshots into bin_atlas_series_<pool>_*.csv.

src/scripts/step10_fetch_triangle_temporal.ts
  Current fetch CLI used by make triangle-temporal.

meteora_bin_atlas/temporal/triangle/fetch.py
  Python wrapper that chooses live vs simulated triangle data.

meteora_bin_atlas/temporal/triangle/render.py
  build_triangle_temporal_mp4 consumes three leg CSVs.

data/triangles/
  Triangle presets. Start with sol_usdc_cbtbtc.
```

The new code should emit CSVs with these columns:

```text
snapshot_index
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

## Ubuntu Setup

Run this on an Ubuntu machine with a stable network connection.

```bash
git clone <repo-url> meteora_bin_atlas
cd meteora_bin_atlas

cp .env.example .env
# Set SOLANA_RPC_URL to a free Alchemy or free Helius Solana mainnet endpoint.
# Also set SOLANA_WS_URL if the provider exposes a separate WebSocket endpoint.

npm install
poetry install
make smoke
```

If `poetry install` is not available:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install poetry
poetry install
```

System packages likely needed for render:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg build-essential pkg-config python3-dev
```

## Implementation Phases

### Phase 1 - Split Fetch and Render Commands

Purpose: make the renderer usable with generated CSVs from any source.

Add Make targets:

```make
triangle-fetch
triangle-render
triangle-record
triangle-materialize
```

Expected behavior:

```bash
make triangle-fetch TRIANGLE=sol_usdc_cbtbtc
make triangle-render TRIANGLE=sol_usdc_cbtbtc
```

`triangle-render` should accept explicit CSV paths:

```bash
make triangle-render \
  TRIANGLE=sol_usdc_cbtbtc \
  LEG0_CSV=data/processed/bin_atlas_series_<pool0>_<stamp>.csv \
  LEG1_CSV=data/processed/bin_atlas_series_<pool1>_<stamp>.csv \
  LEG2_CSV=data/processed/bin_atlas_series_<pool2>_<stamp>.csv \
  FPS=24
```

Implementation notes:

- Add a render-only Python CLI under
  `meteora_bin_atlas/temporal/triangle/render_cli.py` or extend
  `meteora_bin_atlas/temporal/triangle/run.py`.
- It should call `build_triangle_temporal_mp4`.
- It should not call `fetch_triangle_data`.

Acceptance test:

```bash
make triangle-temporal-simulated TRIANGLE=sol_usdc_cbtbtc
make triangle-render TRIANGLE=sol_usdc_cbtbtc FPS=24
```

Expected result:

- MP4 appears under `plots/`.
- No RPC call is made by render-only mode.
- Existing `make triangle-temporal` still works.

### Phase 2 - Account Discovery Snapshot

Purpose: find the DLMM accounts needed by the recorder.

Add:

```text
src/meteora/discoverTriangleAccounts.ts
src/scripts/step11_discover_triangle_accounts.ts
```

CLI:

```bash
npm run discover:triangle-accounts -- --triangle sol_usdc_cbtbtc
```

Output:

```text
data/realtime/sol_usdc_cbtbtc/accounts_<stamp>.json
```

Manifest shape:

```json
{
  "triangle_id": "sol_usdc_cbtbtc",
  "fetched_at_utc": "...",
  "legs": [
    {
      "leg_index": 0,
      "leg_key": "SOL-USDC",
      "pool_address": "...",
      "lb_pair_pubkey": "...",
      "active_bin_id": 123,
      "bin_arrays": [
        {
          "pubkey": "...",
          "bin_array_index": 1,
          "min_bin_id": 70,
          "max_bin_id": 139
        }
      ]
    }
  ]
}
```

Discovery strategy:

1. Resolve triangle preset from `data/triangles/<id>.json`.
2. Create each DLMM pool with the existing SDK path.
3. Fetch `LbPair` state.
4. Fetch current bin arrays around the active bin.
5. Record every bin-array pubkey/index that covers the configured window.

Keep this deliberately narrow at first:

```text
bins_left = 30
bins_right = 30
```

Acceptance test:

```bash
npm run discover:triangle-accounts -- --triangle sol_usdc_cbtbtc --bins-left 30 --bins-right 30
```

Expected result:

- JSON manifest exists under `data/realtime/sol_usdc_cbtbtc/`.
- It contains 3 legs.
- Each leg has 1 pool account and at least 1 bin-array account.
- No private key or wallet config exists anywhere.

### Phase 3 - Forward Account Recorder

Purpose: record account changes from now onward.

Add:

```text
src/meteora/realtimeRecorder.ts
src/scripts/step12_record_triangle_accounts.ts
```

CLI:

```bash
npm run record:triangle -- \
  --triangle sol_usdc_cbtbtc \
  --minutes 30 \
  --bins-left 30 \
  --bins-right 30
```

Storage:

```text
data/realtime/<triangle_id>/<run_id>/
  manifest.json
  accounts.json
  updates.jsonl
  heartbeat.jsonl
```

`updates.jsonl` row shape:

```json
{
  "received_at_utc": "...",
  "slot": 123456,
  "pubkey": "...",
  "account_role": "lb_pair|bin_array",
  "leg_index": 0,
  "leg_key": "SOL-USDC",
  "pool_address": "...",
  "owner": "...",
  "lamports": 123,
  "data_base64": "...",
  "encoding": "base64"
}
```

Recorder behavior:

- Use `SOLANA_WS_URL` if set.
- Otherwise derive WebSocket URL from `SOLANA_RPC_URL` when possible.
- Subscribe to discovered `LbPair` and `BinArray` accounts.
- Append every account update to `updates.jsonl`.
- Write a heartbeat every 10 seconds with update counts.
- Exit cleanly after `--minutes`.
- Flush after every line or every small batch.
- On `SIGINT`, write final manifest status and exit cleanly.

Acceptance test:

```bash
npm run record:triangle -- --triangle sol_usdc_cbtbtc --minutes 2
```

Expected result:

- `manifest.json` says status is `completed` or `interrupted`.
- `heartbeat.jsonl` has regular heartbeat rows.
- If no account updates arrive in 2 minutes, the run still succeeds but reports
  zero updates clearly.
- If Alchemy WebSocket rejects the subscription, the error message says to try
  Helius free tier or fall back to sparse RPC polling.

### Phase 4 - Decode DLMM Account Updates

Purpose: turn raw account bytes into pool/bin state.

Add:

```text
src/meteora/dlmmDecode.ts
src/scripts/step13_decode_triangle_updates.ts
```

CLI:

```bash
npm run decode:triangle-updates -- \
  --run-dir data/realtime/sol_usdc_cbtbtc/<run_id>
```

Output:

```text
decoded_lb_pair.jsonl
decoded_bin_array.jsonl
```

Decoded `LbPair` row:

```json
{
  "slot": 123456,
  "received_at_utc": "...",
  "leg_index": 0,
  "pool_address": "...",
  "active_bin_id": 123,
  "bin_step": 25,
  "token_x_mint": "...",
  "token_y_mint": "..."
}
```

Decoded `BinArray` row:

```json
{
  "slot": 123456,
  "received_at_utc": "...",
  "leg_index": 0,
  "pool_address": "...",
  "bin_array_pubkey": "...",
  "bin_array_index": 1,
  "bins": [
    {
      "position": 0,
      "bin_id": 70,
      "amount_x": "0",
      "amount_y": "0",
      "liquidity_supply": "0"
    }
  ]
}
```

Implementation notes:

- Prefer the Meteora DLMM SDK account coders or exported account layouts.
- Do not hand-roll binary offsets unless the SDK does not expose a stable
  decoder.
- Add fixture tests from one saved raw update.
- If decoding cannot be done cleanly from SDK exports, write a small note in
  this file and switch Phase 3 to persist SDK-normalized snapshots instead.

Acceptance test:

```bash
npm run decode:triangle-updates -- --run-dir data/realtime/sol_usdc_cbtbtc/<run_id>
```

Expected result:

- Decoded files are created.
- `active_bin_id` changes are visible if any pool account updates arrived.
- Bin rows match the numeric shape used by `normalizeBins.ts`.

### Phase 5 - Materialize 24 FPS CSVs

Purpose: convert irregular account updates into fixed-frame series.

Add:

```text
src/meteora/materializeTriangleFrames.ts
src/scripts/step14_materialize_triangle_frames.ts
```

CLI:

```bash
npm run materialize:triangle -- \
  --run-dir data/realtime/sol_usdc_cbtbtc/<run_id> \
  --from 2026-06-19T00:00:00Z \
  --to 2026-06-19T00:30:00Z \
  --fps 24
```

Output:

```text
data/processed/bin_atlas_series_<pool0>_<stamp>.csv
data/processed/bin_atlas_series_<pool1>_<stamp>.csv
data/processed/bin_atlas_series_<pool2>_<stamp>.csv
data/processed/triangle_realtime_<triangle_id>_<stamp>.json
```

Resampling rule:

Use hold-last-observation.

Do not interpolate:

- `liquidity`
- `x_amount`
- `y_amount`
- `active_bin_id`

Reason: interpolation invents on-chain states that never existed. Smoothness
belongs in the visual layer: trails, persistence, easing, and camera choices.

Frame count:

```text
frame_count = ceil((to - from) * fps)
30 minutes @ 24 fps = 43,200 frames per leg
3 legs x 43,200 frames = 129,600 frame snapshots
```

Acceptance test:

```bash
npm run materialize:triangle -- \
  --run-dir data/realtime/sol_usdc_cbtbtc/<run_id> \
  --fps 24
```

Expected result:

- Three CSVs are created.
- Each CSV has the same number of distinct `snapshot_index` values.
- `snapshot_index` starts at 0 and increments by 1.
- `fetched_at_utc` advances by roughly `1 / fps` seconds.
- The CSV header matches `BIN_ATLAS_SERIES_CSV_HEADERS`.

### Phase 6 - Render Preview and Final

Purpose: keep iteration cheap and final render high quality.

Preview command:

```bash
make triangle-render \
  TRIANGLE=sol_usdc_cbtbtc \
  LEG0_CSV=<csv0> \
  LEG1_CSV=<csv1> \
  LEG2_CSV=<csv2> \
  FPS=24 \
  OUTPUT_FRAMES=720 \
  DPI=80
```

Final command:

```bash
make triangle-render \
  TRIANGLE=sol_usdc_cbtbtc \
  LEG0_CSV=<csv0> \
  LEG1_CSV=<csv1> \
  LEG2_CSV=<csv2> \
  FPS=24 \
  DPI=160
```

Acceptance test:

- Preview MP4 renders in a reasonable time on Ubuntu.
- Final MP4 duration equals `frame_count / 24`.
- Footer clock advances with precise UTC labels.
- No leg is accidentally using a stale `*_test.csv`.
- Explicit CSV paths take priority over latest-file lookup.

### Phase 7 - Resume and Cron

Purpose: make the Ubuntu job boring.

Add resume behavior:

```bash
npm run record:triangle -- \
  --triangle sol_usdc_cbtbtc \
  --minutes 30 \
  --resume data/realtime/sol_usdc_cbtbtc/<run_id>
```

Add a shell script:

```text
scripts/run_triangle_realtime_drop.sh
```

It should:

1. Start a 30 minute recording run.
2. Decode updates.
3. Materialize 24 fps CSVs.
4. Render a short preview.
5. Optionally render the full MP4 when `FINAL=1`.
6. Write a final run summary.

Example cron:

```cron
15 */6 * * * cd /opt/meteora_bin_atlas && /usr/bin/env FINAL=0 scripts/run_triangle_realtime_drop.sh >> logs/triangle_realtime.log 2>&1
```

Acceptance test:

```bash
FINAL=0 scripts/run_triangle_realtime_drop.sh
```

Expected result:

- One run directory under `data/realtime/`.
- Three processed CSVs.
- One preview MP4.
- One summary manifest.
- Nonzero exit on failed record/decode/materialize/render step.

## Fallback: RPC Poll Recorder

If account subscriptions are blocked, implement a simple forward recorder using
the existing bounded fetch path. This will not produce true 24 fps market data,
but it is useful for validation and visual QA.

Command:

```bash
npm run fetch:triangle-temporal -- \
  --triangle sol_usdc_cbtbtc \
  --dataset alchemy \
  --count 600 \
  --interval-sec 0 \
  --rpc-backoff-sec 0 \
  --bins-left 30 \
  --bins-right 30
```

Use this fallback to test:

- CSV schema compatibility
- render-only command
- latest-file selection
- Ubuntu ffmpeg/render dependencies

Do not label this as 24 fps market capture. It is sparse polling.

## Free Provider Decision Gate

After Phase 3, decide which free source is good enough.

Alchemy free tier is enough if:

- WebSocket subscriptions stay connected for 30+ minutes.
- Account updates arrive with useful slot metadata.
- Reconnect/resubscribe works.
- No silent data gaps appear during active periods.
- The job stays inside the free monthly and per-second budgets.

Try Helius free tier if:

- Alchemy does not support the needed account subscriptions.
- WebSocket connections drop or throttle.
- Helius free Enhanced WebSockets or free LaserStream access is available on
  mainnet for this account.
- Helius remains within free credits and request limits.

Use sparse RPC polling if:

- Neither free WebSocket path is reliable.
- Free limits are too tight for continuous recording.
- The operator wants simple cron artifacts with no daemon.

Do not switch to paid Geyser, paid LaserStream, or paid archive replay in this
plan. Instead, reduce ambition:

```text
continuous account recorder -> best free path
cron account recorder       -> good free path
sparse RPC poller           -> acceptable free fallback
simulated series            -> visual QA only
```

The code should keep source and materialization separate so source changes do
not affect `bin_atlas_series_*.csv` or `build_triangle_temporal_mp4`.

## Definition of Done

The realtime implementation is done when this works on Ubuntu:

```bash
make smoke
npm run record:triangle -- --triangle sol_usdc_cbtbtc --minutes 30
npm run decode:triangle-updates -- --run-dir <run_dir>
npm run materialize:triangle -- --run-dir <run_dir> --fps 24
make triangle-render TRIANGLE=sol_usdc_cbtbtc LEG0_CSV=<csv0> LEG1_CSV=<csv1> LEG2_CSV=<csv2> FPS=24
```

And the output includes:

- `updates.jsonl`
- decoded account state files
- three normalized `bin_atlas_series_*.csv` files
- one triangle MP4
- one manifest with source, time range, fps, frame count, gaps, and CSV paths

## Notes for Cursor

Keep each phase small and testable.

Do not start by building a general Solana indexer. Start with one triangle and
the exact CSV shape the renderer already understands.

Do not hide data gaps. Report them in the manifest:

```json
{
  "gap_count": 2,
  "max_gap_seconds": 14.2,
  "frames_with_stale_state": 381
}
```

Do not optimize the final render first. First prove:

```text
account updates -> decoded state -> 24 fps CSV -> existing renderer
```
