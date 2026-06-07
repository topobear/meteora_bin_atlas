import { PublicKey } from "@solana/web3.js";

function isBnLike(value: unknown): value is { toString: (base?: number) => string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "constructor" in value &&
    (value as { constructor: { name: string } }).constructor.name === "BN"
  );
}

export function serializeForJson(value: unknown): unknown {
  if (value === null || value === undefined) {
    return value;
  }

  if (value instanceof PublicKey) {
    return value.toBase58();
  }

  if (typeof value === "bigint") {
    return value.toString();
  }

  if (Array.isArray(value)) {
    return value.map((item: unknown) => serializeForJson(item));
  }

  if (isBnLike(value)) {
    return String(value);
  }

  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const serialized: Record<string, unknown> = {};

    for (const [key, nestedValue] of Object.entries(record)) {
      serialized[key] = serializeForJson(nestedValue);
    }

    return serialized;
  }

  return value;
}
