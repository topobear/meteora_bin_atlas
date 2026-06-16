import { writeCsv } from "../io/writeCsv.js";
import { writeJson } from "../io/writeJson.js";
import {
  fetchTriangleSeries,
  legSeriesManifest,
  type TriangleSeriesManifest,
} from "../meteora/fetchTriangleSeries.js";
import {
  BIN_ATLAS_SERIES_CSV_HEADERS,
  normalizeSnapshotSeries,
} from "../meteora/normalizeSnapshotSeries.js";
import type { TriangleLegInput } from "../meteora/fetchTriangleSeries.js";

export type NormalizedTriangleSeries = {
  manifest: TriangleSeriesManifest;
  manifestPath: string;
  legCsvPaths: string[];
};

export async function normalizeTriangleSeries(
  manifest: TriangleSeriesManifest,
  projectRoot: string,
  runTimestamp: string,
): Promise<NormalizedTriangleSeries> {
  const legCsvPaths: string[] = [];

  for (const leg of manifest.legs) {
    const legManifest = legSeriesManifest(manifest, leg);
    const { rows, result } = await normalizeSnapshotSeries(legManifest, projectRoot);
    const csvPath = `${projectRoot}/data/processed/${result.outputStem}.csv`;
    await writeCsv(csvPath, [...BIN_ATLAS_SERIES_CSV_HEADERS], rows);
    legCsvPaths.push(csvPath);
  }

  const manifestStem = `triangle_series_${manifest.triangle_id}_${runTimestamp}`;
  const manifestPath = `${projectRoot}/data/processed/${manifestStem}.json`;
  await writeJson(manifestPath, manifest);

  return {
    manifest,
    manifestPath,
    legCsvPaths,
  };
}

export type { TriangleLegInput };
