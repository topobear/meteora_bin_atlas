import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

function escapeCsvValue(value: string | number | ""): string {
  const text = String(value);
  if (text.includes(",") || text.includes('"') || text.includes("\n")) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

export async function writeCsv(
  filePath: string,
  headers: string[],
  rows: Array<Record<string, string | number | "">>,
): Promise<void> {
  await mkdir(path.dirname(filePath), { recursive: true });

  const lines = [
    headers.join(","),
    ...rows.map((row) => headers.map((header) => escapeCsvValue(row[header] ?? "")).join(",")),
  ];

  await writeFile(filePath, `${lines.join("\n")}\n`, "utf8");
}
