# How AI tools were used

This project was built with AI coding assistants (Claude Code) working under my direction. I set the requirements
and the architecture, made the trade-off decisions recorded in `docs/adr/`, and reviewed and ran everything.

The rules I held the work to, which a reviewer can check rather than take on trust:
- **Nothing is claimed without a script that measures it.** Every number in the README comes from
  `docs/results/*.json`, written by a script in `scripts/` or `evals/`. Unmeasured things are listed in
  `docs/Limitations.md` rather than estimated.
- **Tests and CI are the review gate.** More than 600 Python tests, 85 gateway tests, end-to-end suites against a
  real stack, a guardrail eval with a regression gate, Semgrep, gitleaks, ZAP, and a kind-cluster `helm test` run on
  every push.
- **Deployment found bugs that tests didn't, and each became a regression test.** Examples are in the CHANGELOG
  under "Fixed": the audit chain forking across tenants, unredacted MCP tool arguments, and NUL bytes making ledger
  entries unwritable.
- **Attribution is preserved.** v1 was Aegis by infyier (MIT); see `LICENSE` and `CHANGELOG.md`.
