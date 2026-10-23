# Deployment

Three ways to run VEYRQUN, from a laptop to a cluster. All use the same images.

## 1. Local (docker compose), about 5 minutes

```bash
cp .env.example .env
docker compose up -d --build          # postgres, redis, keycloak, control plane, gateway, audit writer, mocks, UI
python deploy/keycloak/configure_realm.py && python deploy/keycloak/seed_dev_users.py --out .dev-users.json
open http://localhost:5173            # sign in with a seeded user (passwords + TOTP seeds in .dev-users.json)
```

For the no-login demo experience locally, set `DEMO_MODE=1` and `DEMO_EXPIRES_AT=<RFC 3339>` on the control plane.

## 2. Any Kubernetes cluster (Helm)

```bash
helm install veyrqun deploy/helm/veyrqun -n veyrqun --create-namespace \
  --set image.registry=<registry> --set image.tag=<tag> --set publicUrl=https://<host>
helm test veyrqun -n veyrqun          # health, unauthenticated refusal, one allowed + one policy-blocked tool call
```

- **What the chart gives you:**
  - Control plane, gateway (HPA, PDB, topology spread, graceful drain), a single-leader audit writer, nginx entry
    point, and optional mocks.
  - Migrations run as a Job with the owner role; the app role can't UPDATE or DELETE the ledger.
  - Default-deny NetworkPolicies: only the gateway reaches model and tool upstreams.
  - Restricted pod security throughout (non-root, read-only root filesystem, no capabilities).
- **Production:** set `postgres.bundled=false` and `redis.bundled=false`, then provide `database-url`,
  `migration-database-url` and `redis-url` in a Secret (`secrets.existingSecret`), along with the keys listed in
  `templates/secret.yaml`. Point `gateway.upstream.*` at your model provider and tool servers.
- **CI** installs the chart on kind and runs `helm test` on every push.

## 3. GKE Autopilot (the public demo)

```bash
gcloud services enable artifactregistry.googleapis.com aiplatform.googleapis.com compute.googleapis.com
gcloud artifacts repositories create veyrqun --repository-format=docker --location=us-central1
gcloud compute addresses create veyrqun-demo-ip --global
# Vertex AI access for the gateway's Kubernetes service account, via Workload Identity (no keys):
gcloud projects add-iam-policy-binding <project> --role=roles/aiplatform.user --condition=None \
  --member="principal://iam.googleapis.com/projects/<number>/locations/global/workloadIdentityPools/<project>.svc.id.goog/subject/ns/veyrqun/sa/veyrqun-gateway"
# build + push the four images, then:
helm upgrade --install veyrqun deploy/helm/veyrqun -n veyrqun --create-namespace -f deploy/helm/veyrqun/values-gke.yaml \
  --set image.tag=<tag> --set publicUrl=https://<ip>.nip.io --set ingress.host=<ip>.nip.io \
  --set demo.expiresAt=<RFC3339> --set demo.scaleDownSchedule="<m> <h> <day> <month> *"
python scripts/smoke_public.py https://<ip>.nip.io     # 14 checks as a visitor -> docs/results/gke_demo.json
```

The demo refuses guests after `demo.expiresAt`, and a CronJob scales the release to zero shortly afterwards. To
remove everything, follow [runbooks/demo-teardown.md](runbooks/demo-teardown.md).
