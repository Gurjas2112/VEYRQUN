# Deployment

## Local (two terminals)

```bash
pip install -r requirements.txt
python -m uvicorn --app-dir control-plane app:app --host 0.0.0.0 --port 8000      # API  → http://localhost:8000/docs

cd frontend && npm install && npm run dev                          # UI   → http://localhost:5173
```

Or one command from the repo root after `npm install` in both root and `frontend/`: `npm start` (or `./start.sh`).

Reset all state: stop the backend and delete `control-plane/veyrqun.db` (plus `-wal`/`-shm`). It is recreated and re-seeded.

## Docker

```bash
docker compose up --build
```

* `veyrqun-backend` → `:8000`, database persisted in the `veyrqun-data` volume at `/data/veyrqun.db`
* `veyrqun-frontend` → `:5173` (static build served by nginx)

## Cloud

* **Frontend (Vercel):** root `frontend`, preset Vite, env `VITE_API_BASE_URL=https://<backend-host>`. `vercel.json`
  handles SPA rewrites.
* **Backend (Render / Railway / Fly):** working dir `backend`, build `pip install -r requirements.txt`, start
  `uvicorn app:app --host 0.0.0.0 --port $PORT`. Set `ALLOWED_ORIGINS` to the frontend origin and mount a disk for
  `VEYRQUN_DB_FILE`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `VEYRQUN_DB_FILE` | `control-plane/veyrqun.db` | SQLite path |
| `VEYRQUN_SIMULATOR` | `1` | `0` disables the background agent simulator |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Frontend → backend URL (build time) |

## Production hardening checklist

See [Technical Debt](Technical%20Debt.md): authN/Z on the control plane, PostgreSQL, Redis rate windows,
WORM audit storage, Prometheus/Grafana scraping of `/metrics/prometheus`.
