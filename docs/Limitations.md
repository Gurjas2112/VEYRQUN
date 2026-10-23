# Limitations and roadmap

Stated plainly, so nobody has to discover them.

## Known limitations
- **Injection detection is pattern-based.** It catches known override phrasing, including obfuscated forms, but not
  paraphrases (`evals/`: 14/16). The planned fix is a classifier behind the same `injection_score` field, with a
  strict timeout; a null score never blocks.
- **The eval corpus is small and author-written** (75 cases). It is a regression gate, not evidence of robustness
  against a determined red team.
- **Rotating an agent secret doesn't revoke tokens already issued** (they live up to 15 minutes). Stop and revoke do
  take effect immediately. Fix: a per-agent `tokens_valid_after` checked by the gateway (ADR 0016).
- **One global audit chain with one writer.** Throughput is bounded by serial appends. Per-tenant chains are the
  scaling path (ADR 0012); this hasn't been load-tested beyond the k6 runs in `docs/results/`.
- **Off-database anchor copies** go to S3-compatible storage without Object Lock unless the bucket provides it.
- **The demo's model price** comes from public third-party listings (USD 1.50 / 9.00 per million tokens), not
  Google's own table. It errs high, which suits a budget cap.
- **The Helm chart's bundled Postgres** has no backups. Use a managed database in production.
- **Tested:**
  - Single region only.
  - No chaos testing beyond the failure-contract integration tests: killing pods mid-stream has not been exercised
    at load.

## Roadmap
1. Token revocation on secret rotation; per-agent model allowlist in Rego.
2. Guard classifier for `injection_score`, plus a larger red-team corpus from real transcripts.
3. OpenTelemetry traces agent → gateway → upstream, Grafana dashboards and alert rules as code, k6 soak and spike runs.
4. Governed retrieval (permission-filtered RAG) with its own eval.
5. Per-tenant audit chains when one writer becomes the bottleneck.
