import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

export async function writeJson(filePath: string, data: unknown): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });
  const serialized = `${JSON.stringify(data, null, 2)}\n`;
  await writeFile(filePath, serialized, "utf8");
}
