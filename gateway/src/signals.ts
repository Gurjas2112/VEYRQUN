/**
 * Signals are inputs to the policy, never decisions (AGENTS.md rule 2). Rego compares them with thresholds in the
 * bundle (`settings.injection_block_threshold`) and decides.
 *
 * injection_score v0 is a transparent phrase heuristic for well-known instruction-override patterns, in user text,
 * tool results (indirect injection) and tool arguments. It is cheap (one pass of a few regexes) and easy to audit.
 * Phase 9's guard classifier can replace it behind the same field; on timeout it must return null, which never blocks.
 */

import { canonicalize, decodedBase64Segments } from "./normalize.js";

const PATTERNS: Array<{ re: RegExp; score: number; id: string }> = [
  { id: "override_previous", score: 0.95,
    re: /\b(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|system)\s+(instructions?|rules|prompts?|polic(y|ies))/i },
  { id: "reveal_system_prompt", score: 0.9,
    re: /\b(reveal|print|show|repeat|leak)\s+(me\s+)?(your|the)\s+(system\s+prompt|hidden\s+instructions|initial\s+instructions)/i },
  { id: "role_hijack", score: 0.9,
    re: /\b(you\s+are\s+now|from\s+now\s+on\s+you\s+are|act\s+as|pretend\s+to\s+be)\b[^.\n]{0,40}\b(admin(istrator)?|developer\s+mode|DAN|unrestricted|jailbroken|root)\b/i },
  { id: "fake_system_block", score: 0.9, re: /(BEGIN|START)\s+(SYSTEM|ADMIN|DEVELOPER)\s+(PROMPT|OVERRIDE|INSTRUCTIONS)/i },
  { id: "new_instructions", score: 0.7, re: /\b(new|updated|real)\s+instructions?\s*:/i },
];

export interface InjectionSignal {
  score: number;
  matched: string[];
}

export function injectionSignal(text: string): InjectionSignal {
  // Canonical text defeats zero-width, fullwidth and look-alike tricks; base64 payloads are decoded and checked too.
  const canon = canonicalize(text).text;
  const views = [canon, ...decodedBase64Segments(canon).map((d) => canonicalize(d).text)];
  let score = 0;
  const matched: string[] = [];
  for (const p of PATTERNS) {
    for (const [i, view] of views.entries()) {
      if (!p.re.test(view)) continue;
      const id = i === 0 ? p.id : `${p.id}:base64`;
      if (!matched.includes(id)) matched.push(id);
      score = Math.max(score, p.score);
    }
  }
  return { score, matched };
}
