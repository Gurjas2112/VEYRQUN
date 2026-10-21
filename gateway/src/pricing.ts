/**
 * LLM spend: estimate before the call (so caps apply up front), correct from `usage` afterwards.
 * Rounded *up* to whole minor units: governance overestimates rather than letting fractions slip through.
 */
export interface ModelPrice {
  input_per_mtok: number;
  output_per_mtok: number;
}

export interface ChatMessage {
  role: string;
  content?: unknown;
}

export function messageText(messages: ChatMessage[]): string {
  return messages
    .map((m) => (typeof m.content === "string" ? m.content : Array.isArray(m.content)
      ? m.content.map((p) => (p && typeof p === "object" && "text" in p ? String((p as { text: unknown }).text) : "")).join(" ")
      : ""))
    .join("\n");
}

/** ~4 characters per token is the usual heuristic for English; only used for the pre-call estimate. */
export const estimateTokens = (text: string): number => Math.ceil(text.length / 4) + 4;

export function costMinor(price: ModelPrice, promptTokens: number, completionTokens: number, exponent = 2): number {
  const usd = (promptTokens * price.input_per_mtok + completionTokens * price.output_per_mtok) / 1_000_000;
  return Math.ceil(usd * 10 ** exponent);
}

export function priceFor(prices: Record<string, ModelPrice>, model: string): ModelPrice | undefined {
  return prices[model];
}
