export function rpcErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function isRpcDatasetError(error: unknown): boolean {
  const message = rpcErrorMessage(error);
  const lower = message.toLowerCase();

  return (
    message === "fetch failed" ||
    lower.includes("429") ||
    lower.includes("too many") ||
    lower.includes("rate limit") ||
    lower.includes("max usage") ||
    lower.includes("exceeded") ||
    lower.includes("quota") ||
    lower.includes("credit") ||
    lower.includes("unauthorized") ||
    lower.includes("forbidden") ||
    lower.includes("401") ||
    lower.includes("403") ||
    lower.includes("-32005")
  );
}

export function formatRpcDatasetWarning(error: unknown, dataset: string): string {
  const message = rpcErrorMessage(error);
  const lower = message.toLowerCase();

  if (lower.includes("max usage") || lower.includes("credit") || lower.includes("quota")) {
    return `${message} (${dataset} credits or quota exhausted)`;
  }

  if (
    lower.includes("401") ||
    lower.includes("403") ||
    lower.includes("unauthorized") ||
    lower.includes("forbidden")
  ) {
    return `${message} (${dataset} API key or access issue — check SOLANA_RPC_URL)`;
  }

  if (lower.includes("429") || lower.includes("too many") || lower.includes("rate limit")) {
    return `${message} (${dataset} rate limit — try a private RPC or --dataset simulated)`;
  }

  if (message === "fetch failed") {
    return `${message} (network or RPC unreachable for ${dataset})`;
  }

  return `${message} (${dataset} RPC error)`;
}
