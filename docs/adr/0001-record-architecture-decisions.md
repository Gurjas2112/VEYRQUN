# 0001. Record architecture decisions

**Status:** accepted · 2026-10-22

## Context
VEYRQUN v3 makes many decisions (latency budgets, failure modes, consistency) that reviewers and future
contributors need to see with their reasoning, not just their outcome.

## Decision
Record each non-obvious decision as an ADR in `docs/adr/NNNN-title.md` with sections Context, Decision,
Consequences, Alternatives considered. ADRs are immutable once accepted; a later ADR supersedes an earlier one.

## Consequences
Small writing overhead per decision; interviews and reviews can follow the reasoning.
