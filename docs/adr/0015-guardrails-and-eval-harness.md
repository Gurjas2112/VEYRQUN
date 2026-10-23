# 0015. Guardrails are deterministic and measured, not asserted

**Status:** accepted · 2026-10-23

## Context
"We block prompt injection and PII" is easy to claim and hard to verify. Pattern-based guardrails fail on the
first obfuscated input, and a guardrail that blocks normal work gets switched off. So every guardrail here runs
against a labelled corpus, and the README quotes only those numbers.

## Decisions

**Guardrails live in the gateway and are deterministic.** Detectors emit data classes and signals. Rego and the
agent's policy decide what happens: allow, redact, block or hold for approval. No model makes or overrides a
decision (rule 2). The layers are:
- **Canonical text before detection.** NFKC, invisible characters stripped, Cyrillic and Greek look-alikes
  mapped to Latin. Spans are mapped back to the original text, so redaction masks exactly the right characters.
- **Structured arguments are decoded before classification.** Classification sees JSON values, not their
  encoding, in which a line break is `\n` and splits a card number.
- **Request-side redaction is applied inside MCP tool arguments**, so the tool never receives masked classes.
  Building the harness found that tool arguments were forwarded unmasked; only the echoed reply was being masked.
- **Data classes:** cards (Luhn, any consistent separator), Aadhaar (Verhoeff), IBAN (mod 97), Indian PAN,
  email, phone, and **`secret`** (cloud/API keys, private keys, tokens, JWTs, `password=` assignments). Every
  fleet agent blocks `secret`.
- **Injection signal:** transparent phrase rules evaluated on canonical text and on base64 payloads that decode to
  readable text. Rego blocks at or above `settings.injection_block_threshold`.
- **Resource limits:** `max_tokens` ceiling, prompt-size limit (413), tool-call fan-out cap per model turn, and the
  global daily model budget (ADR 0014 / `LLM_DAILY_BUDGET_MINOR`).

**The eval harness replays the corpus through the real path.** `python -m evals.run` boots the stack, logs in as a
demo guest and sends every case through `POST /playground/run`. That route mints an agent token and calls the
gateway, so the run includes classification, signals, OPA, Redis limits and output scanning.

The corpus (`evals/cases.py`) has 42 must-block cases (direct and indirect injection, card numbers, secrets,
tool misuse, limits), 3 must-redact cases and 30 benign cases written to *look* risky: "ignore the previous booking",
card-like order references, "password reminder email".

**CI gate.** `--gate` fails the build if any case in `evals/baseline.json` is no longer stopped, or if the benign
false-positive rate rises. The baseline changes only in a reviewed commit.

## Results (`docs/results/guardrail_eval_before.json` → `docs/results/guardrail_eval.json`)

| | before | after |
|---|---|---|
| must-block stopped | 23/42 (54.8%) | 40/42 (95.2%) |
| benign false positives | 0/30 | 0/30 |
| secrets | 0/8 | 8/8 |
| obfuscated card numbers | 5/10 | 10/10 |
| prompt injection | 10/16 | 14/16 |

## Known misses (kept in the corpus on purpose)
- `inj-06` "New instructions: …" scores 0.7, below the 0.9 threshold. The phrase also appears in ordinary requests,
  so lowering the threshold would buy this case with false positives.
- `inj-13` is a paraphrase ("kindly set aside the earlier guidance…"). Phrase rules can't catch meaning. This is
  what the planned classifier is for (Phase 9). It plugs in behind the same `injection_score` field, with a strict
  timeout, and a null score never blocks.
- The corpus is small (75 cases) and written by the author. That makes it a regression gate, not proof of robustness
  against a motivated attacker. Adding real red-team transcripts is the next step.
