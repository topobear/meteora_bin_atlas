import { readFile } from "node:fs/promises";
import path from "node:path";

import { formatTimestampForFilename } from "./discoverPools.js";
import {
  BIN_ATLAS_CSV_HEADERS,
  binAtlasRowsToCsvRows,
  normalizeBinArrays,
  type RawBinArraysFile,
} from "./normalizeBins.js";
import type { SnapshotSeriesManifest } from "./fetchSnapshotSeries.js";

export const BIN_ATLAS_SERIES_CSV_HEADERS = [
  "snapshot_index",
  ...BIN_ATLAS_CSV_HEADERS,
] as const;

export type NormalizeSnapshotSeriesResult = {
  rowCount: number;
  snapshotCount: number;
  outputStem: string;
};

export async function normalizeSnapshotSeries(
  manifest: SnapshotSeriesManifest,
  projectRoot: string,
): Promise<{
  rows: Array<Record<string, string | number | "">>;
  result: NormalizeSnapshotSeriesResult;
}> {
  const rawDir = path.join(projectRoot, "data", "raw");
  const combinedRows: Array<Record<string, string | number | "">> = [];

  for (const snapshot of manifest.snapshots) {
    const rawPath = path.join(rawDir, snapshot.raw_filename);
    const contents = await readFile(rawPath, "utf8");
    const file = JSON.parse(contents) as RawBinArraysFile;
    const rows = normalizeBinArrays(file);

    for (const row of binAtlasRowsToCsvRows(rows)) {
      combinedRows.push({
        snapshot_index: snapshot.index,
        ...row,
      });
    }
  }

  const outputStem = `bin_atlas_series_${manifest.pool_address}_${formatTimestampForFilename(
    new Date(manifest.series_completed_at_utc),
  )}`;

  return {
    rows: combinedRows,
    result: {
      rowCount: combinedRows.length,
      snapshotCount: manifest.snapshots.length,
      outputStem,
    },
  };
}
