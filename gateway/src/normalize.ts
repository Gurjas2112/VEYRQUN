/**
 * Canonical text for detection. Attackers dodge pattern-based guardrails with characters that look the same to a
 * model but not to a regex: fullwidth digits/letters, non-breaking or zero-width spaces, Cyrillic/Greek look-alikes.
 * Detectors run on the canonical form; `map[i]` is the index in the original string of canonical char i, so spans
 * found in the canonical text redact exactly the right characters of the original.
 */
import { INVISIBLE } from "./normalize-chars.js";


// Common confusables (Cyrillic and Greek letters that render like Latin ones).
const CONFUSABLES: Record<string, string> = {
  "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
  "І": "I", "Ј": "J", "Ѕ": "S", "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
  "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
  "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "ο": "o", "ν": "v",
};

export interface Canonical {
  text: string;
  map: number[]; // canonical index -> original index
}

export function canonicalize(input: string): Canonical {
  let text = "";
  const map: number[] = [];
  let i = 0;
  for (const ch of input) { // iterates code points, so surrogate pairs stay together
    const at = i;
    i += ch.length;
    if (INVISIBLE.test(ch)) continue;
    const mapped = CONFUSABLES[ch] ?? ch.normalize("NFKC");
    for (let k = 0; k < mapped.length; k++) {
      text += mapped[k];
      map.push(at);
    }
  }
  map.push(input.length); // sentinel so an end offset of text.length maps to the end of the input
  return { text, map };
}

/** Original [start, end) for a canonical [start, end). */
export function toOriginal(c: Canonical, start: number, end: number, original: string): [number, number] {
  const s = c.map[start] ?? original.length;
  // End: one past the original index of the last canonical char, extended over that char's full code point.
  const last = c.map[end - 1] ?? s;
  const cp = original.codePointAt(last);
  return [s, last + (cp !== undefined && cp > 0xffff ? 2 : 1)];
}

/** Base64 runs that decode to readable text: hidden instructions an agent might be told to "decode and follow". */
export function decodedBase64Segments(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(/[A-Za-z0-9+/]{16,}={0,2}/g)) {
    try {
      const decoded = Buffer.from(m[0], "base64").toString("utf8");
      if (decoded.length >= 8 && /^[\x20-\x7e\s]+$/.test(decoded)) out.push(decoded);
    } catch {
      /* not base64 */
    }
  }
  return out;
}
