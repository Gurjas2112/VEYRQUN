# Runbook: tear down the public demo

The demo stops itself: after `demo.expiresAt` guests get `410 Gone`, and the `veyrqun-demo-expiry` CronJob scales
every workload to zero. What remains billable: the persistent disks, the static IP, the load balancer forwarding rules
and the images. Remove them all:

```bash
helm uninstall veyrqun -n veyrqun                       # workloads, services, ingress (and its load balancer)
kubectl delete pvc -n veyrqun --all                     # Postgres and Redis disks (deletes the demo data)
kubectl delete secret veyrqun-secrets -n veyrqun        # kept by Helm on purpose (resource-policy: keep)
kubectl delete namespace veyrqun
gcloud compute addresses delete veyrqun-demo-ip --global
gcloud artifacts repositories delete veyrqun --location=us-central1
gcloud projects remove-iam-policy-binding <project> --role=roles/aiplatform.user --condition=None \
  --member="principal://iam.googleapis.com/projects/<number>/locations/global/workloadIdentityPools/<project>.svc.id.goog/subject/ns/veyrqun/sa/veyrqun-gateway"
# optional: stop Vertex AI usage entirely
gcloud services disable aiplatform.googleapis.com
```

To extend the demo instead, upgrade with a later `demo.expiresAt` and a matching `demo.scaleDownSchedule`.

**Verify it's gone:** `gcloud compute forwarding-rules list` and `gcloud compute disks list` show nothing for
`veyrqun`. The URL stops resolving to a backend.
