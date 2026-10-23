# Kubernetes deployment evidence (GKE Autopilot)

Recorded 2026-10-23T11:13Z from `scripts` and `helm test` output; nothing here is hand-edited.

- Cluster: `veyrquin-cluster` (GKE Autopilot v1.36.4-gke.1391002, us-central1), namespace `veyrqun`
- Chart: `deploy/helm/veyrqun` with `values-gke.yaml`, image tag `v3-202610231111`
- Public entry: https://136.82.10.126.nip.io (global static IP, Google-managed certificate, HTTPS redirect)
- Model upstream: Gemini 3.5 Flash on Vertex AI via Workload Identity (no keys), USD 5/day budget

## Pods

```
veyrqun-audit-writer 1/1 Running
veyrqun-control-plane 1/1 Running
veyrqun-dashboard 1/1 Running
veyrqun-gateway 1/1 Running
veyrqun-gateway 1/1 Running
veyrqun-migrate-9-rzsc4 0/1 Completed
veyrqun-mock-llm 1/1 Running
veyrqun-mock-mcp 1/1 Running
veyrqun-postgres-0 1/1 Running
veyrqun-redis-0 1/1 Running
```

## helm test veyrqun

```
PASS control plane healthy
PASS dashboard serves the app
PASS gateway ready  ({"ready":true,"checks":{"redis":"ok"},"bundle_revisions":{}
PASS unauthenticated agent refused
PASS allowed tool call goes through  ({"jsonrpc":"2.0","id":1,"result":{"content
PASS forbidden tool call blocked by policy  (403 {"jsonrpc":"2.0","id":1,"error"
all checks passed
```

## Public smoke test

`python scripts/smoke_public.py https://136.82.10.126.nip.io` -> `docs/results/gke_demo.json`: 14/14 checks passed, playground round trip median 531.1 ms.

## Found by deploying (fixed, with regression tests)

- The audit chain forked when two workspaces wrote in turn: the head lookup ran under row-level security (migration 0005).
- Images declared `USER node` (non-numeric), so `runAsNonRoot` could not be verified; now uid 1000.
- `VEYRQUN_ENV=staging` was rejected by config validation; the demo runs as `production`.
