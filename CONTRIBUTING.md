# Contributing to VEYRQUN

## Setup

```bash
pip install -r requirements-dev.txt
npm --prefix frontend install
cp .env.example .env          # fill in local values; .env is git-ignored
```

## Tests

```bash
python -m pytest                       # control-plane tests
npm --prefix frontend run lint
npm --prefix frontend run build
```

Later phases add `opa test policies/ -v`, `npm --prefix gateway test`, Playwright end-to-end tests, and `helm lint`.
CI runs all of them on every push and pull request.

## Commits

- [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `ci:`, `chore:`.
- One logical change per commit. Describe *what changed and how to verify it* in the body.

## Architecture decisions

Non-obvious decisions are recorded as [ADRs](https://adr.github.io/) in `docs/adr/NNNN-short-title.md`, with these sections:
Context, Decision, Consequences, Alternatives considered.

## Rules that are never relaxed

1. Attribution to Aegis (infyier) stays in LICENSE and README.
2. Only the deterministic policy engine makes allow/block decisions. Models can emit signals or drafts, nothing more.
3. Default-deny and fail-closed.
4. No made-up numbers. Every metric in the docs comes from a script that writes to `docs/results/*.json`.
5. No secrets in git. Config comes from env vars, documented in `.env.example`. CI runs a secret scan.
6. Money is never a float.

The full specification is in [docs/AGENT_PROMPT.md](docs/AGENT_PROMPT.md).
