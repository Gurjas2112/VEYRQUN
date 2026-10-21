# Contributing to VEYRQUN

## Setup

Toolchain: **Python 3.11+** and **Node 22 LTS** (npm 10, see `.nvmrc`). The lockfiles are generated with
the same npm that CI and the production images use; newer npm majors resolve optional platform packages
differently, so use `npm install` (not `npm ci`) if you develop on a newer Node.

```bash
pip install -r requirements-dev.txt
npm --prefix frontend install
cp .env.example .env          # fill in local values; .env is git-ignored
```

## Tests

```bash
python -m pytest                       # control-plane tests (needs: docker compose up -d postgres redis)
npm --prefix gateway test              # gateway unit + failure-contract tests (needs Redis)
python -m pytest e2e                   # Keycloak login flows + control-plane/gateway interop
npm --prefix frontend run lint
npm --prefix frontend run build
```

Policy: `opa test policies/ -v`, `python scripts/build_policy.py --check`, `python scripts/gen_policy_tests.py --check`.
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
