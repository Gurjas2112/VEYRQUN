/**
 * Classification and redaction must see the *values* inside structured arguments, not their JSON encoding: in
 * JSON.stringify output a line break is the two characters `\n` and unicode can be `\uXXXX`, which splits or hides
 * data from detectors. These helpers walk the decoded structure instead.
 */

/** Every string value (and object key) in `value`, joined by newlines, for classification. */
export function textOf(value: unknown): string {
  const parts: string[] = [];
  const walk = (v: unknown, depth: number): void => {
    if (depth > 32) return;
    if (typeof v === "string") parts.push(v);
    else if (Array.isArray(v)) v.forEach((x) => walk(x, depth + 1));
    else if (v && typeof v === "object") {
      for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
        parts.push(k);
        walk(x, depth + 1);
      }
    }
  };
  walk(value, 0);
  return parts.join("\n");
}

/** A copy of `value` with `fn` applied to every string value (keys are kept). */
export function mapStrings<T>(value: T, fn: (s: string) => string): T {
  const walk = (v: unknown, depth: number): unknown => {
    if (depth > 32) return v;
    if (typeof v === "string") return fn(v);
    if (Array.isArray(v)) return v.map((x) => walk(x, depth + 1));
    if (v && typeof v === "object") {
      return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, walk(x, depth + 1)]));
    }
    return v;
  };
  return walk(value, 0) as T;
}

/** Decoded text of a JSON-encoded string (e.g. tool-call `arguments`), or the raw string if it isn't JSON. */
export function textOfJsonString(raw: string): string {
  try {
    return textOf(JSON.parse(raw));
  } catch {
    return raw;
  }
}
