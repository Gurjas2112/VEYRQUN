/**
 * Data classification: finds personal and payment data in text and returns classes plus spans.
 * Detectors are checksum-validated where a checksum exists (Luhn for cards, Verhoeff for Aadhaar,
 * ISO 7064 mod-97 for IBAN), which keeps false positives down on arbitrary digit runs.
 */

export type DataClass = "email" | "phone" | "aadhaar" | "pan_in" | "iban" | "pci_pan";

export interface Span {
  start: number;
  end: number;
  cls: DataClass;
}

export interface Classification {
  classes: DataClass[];
  spans: Span[];
}

export function luhnValid(digits: string): boolean {
  let sum = 0;
  let double = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = digits.charCodeAt(i) - 48;
    if (double) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
    double = !double;
  }
  return digits.length >= 13 && sum % 10 === 0;
}

const VERHOEFF_D = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
  [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
  [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
  [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
];
const VERHOEFF_P = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
  [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
  [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
];

export function verhoeffValid(digits: string): boolean {
  let c = 0;
  const rev = digits.split("").reverse();
  for (let i = 0; i < rev.length; i++) {
    const row = VERHOEFF_P[i % 8]!;
    c = VERHOEFF_D[c]![row[Number(rev[i])]!]!;
  }
  return c === 0;
}

export function ibanValid(raw: string): boolean {
  const iban = raw.replace(/\s+/g, "").toUpperCase();
  if (!/^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$/.test(iban)) return false;
  const rearranged = iban.slice(4) + iban.slice(0, 4);
  let remainder = 0;
  for (const ch of rearranged) {
    const v = ch >= "A" && ch <= "Z" ? (ch.charCodeAt(0) - 55).toString() : ch;
    for (const d of v) remainder = (remainder * 10 + Number(d)) % 97;
  }
  return remainder === 1;
}

interface Detector {
  cls: DataClass;
  re: RegExp;
  valid?: (match: string) => boolean;
}

const DETECTORS: Detector[] = [
  { cls: "email", re: /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}/g },
  // Card numbers: 13-19 digits, optionally grouped by spaces/dashes, Luhn-checked.
  { cls: "pci_pan", re: /\b(?:\d[ -]?){12,18}\d\b/g, valid: (m) => luhnValid(m.replace(/[ -]/g, "")) },
  // Aadhaar: 12 digits (often 4-4-4), never starting with 0/1, Verhoeff-checked.
  { cls: "aadhaar", re: /\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b/g, valid: (m) => verhoeffValid(m.replace(/[ -]/g, "")) },
  // Indian PAN (tax id): 5 letters, 4 digits, 1 letter; 4th letter encodes the holder type.
  { cls: "pan_in", re: /\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b/g },
  { cls: "iban", re: /\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b/g, valid: ibanValid },
  // Phones: +country form, or Indian 10-digit mobiles (6-9 prefix).
  { cls: "phone", re: /(?:\+\d{1,3}[ -]?)?\b[6-9]\d{4}[ -]?\d{5}\b|\+\d{1,3}[ -]?\d{2,4}[ -]?\d{3,4}[ -]?\d{3,4}\b/g },
];

export function classify(text: string): Classification {
  const spans: Span[] = [];
  for (const d of DETECTORS) {
    d.re.lastIndex = 0;
    for (const m of text.matchAll(d.re)) {
      const value = m[0];
      if (d.valid && !d.valid(value)) continue;
      const start = m.index ?? 0;
      const end = start + value.length;
      // Earlier detectors win on overlap (a card number is not also a phone number).
      if (spans.some((s) => start < s.end && end > s.start)) continue;
      spans.push({ start, end, cls: d.cls });
    }
  }
  spans.sort((a, b) => a.start - b.start);
  return { classes: [...new Set(spans.map((s) => s.cls))].sort(), spans };
}

export function redact(text: string, spans: Span[], classes: ReadonlySet<string>): string {
  let out = "";
  let cursor = 0;
  for (const s of spans) {
    if (!classes.has(s.cls)) continue;
    out += text.slice(cursor, s.start) + `[REDACTED:${s.cls}]`;
    cursor = s.end;
  }
  return out + text.slice(cursor);
}

/** PCI DSS display rule: first 6 and last 4 digits at most. */
export function maskPan(pan: string): string {
  const d = pan.replace(/\D/g, "");
  return d.length < 13 ? "****" : `${d.slice(0, 6)}${"*".repeat(d.length - 10)}${d.slice(-4)}`;
}
