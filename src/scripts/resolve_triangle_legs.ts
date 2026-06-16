import path from "node:path";

import {
  loadTrianglePreset,
  resolveTriangleLegs,
} from "../meteora/resolveTriangleLegs.js";

async function main(): Promise<void> {
  const presetFlagIndex = process.argv.indexOf("--preset");
  const presetPath =
    presetFlagIndex !== -1 && process.argv[presetFlagIndex + 1]
      ? process.argv[presetFlagIndex + 1]
      : path.join(process.cwd(), "data", "triangles", "sol_usdc_weth.json");

  const projectRoot = process.cwd();
  const preset = await loadTrianglePreset(presetPath);
  const resolved = await resolveTriangleLegs(preset, projectRoot);
  console.log(JSON.stringify(resolved));
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`resolve:triangle-legs failed: ${message}`);
  process.exit(1);
});
