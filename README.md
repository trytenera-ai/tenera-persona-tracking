# Tenera Persona Tracking (TPT)

TPT is an open-source persona tracking and cohort analytics service. It provides a FastAPI server and CLI for collecting product behavior by `distinct_id`, attaching flexible persona attributes, replaying sessions, and clustering users into cohorts.

Use it when you want a small, self-hostable analytics layer for persona profiles, event timelines, and cohort discovery.

## Demo

### Personas dashboard

![TPT personas dashboard](docs/demo/personas-dashboard.png)

### Action log

![TPT action log](docs/demo/action-log.png)

## What TPT provides

- **Persona profiles** — one record per tracked identity (`distinct_id`) with arbitrary key/value entities such as plan, role, company, industry, lifecycle stage, or project context.
- **Event timelines** — PostHog-style event ingestion through `/api/v1/track`, with optional properties, timestamps, and screenshots.
- **Session replay** — rrweb session ingestion and replay for watching the exact browser journey behind a persona's activity.
- **Cohort clustering** — k-means, HDBSCAN, and k-prototypes clustering over persona entities, with optional LLM-generated cluster names and summaries.
- **CLI + REST API** — the `tpt` CLI uses the same documented API that external tools can call.
- **Local or production storage** — SQLite for local development; Supabase/Postgres for production deployments.

## Repository

```bash
git clone git@github.com:trytenera-ai/tenera-persona-tracking.git
cd tenera-persona-tracking
```

If your GitHub SSH key is not configured, use HTTPS instead:

```bash
git clone https://github.com/trytenera-ai/tenera-persona-tracking.git
```

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
cp .env.example .env
```

Edit `.env` and set at least:

```dotenv
API_KEY=your-read-write-admin-key
WRITE_KEY=your-browser-safe-ingestion-key
DATABASE_MODE=sqlite
```

Start the service:

```bash
tpt serve
```

Open:

- Dashboard: <http://localhost:8000/>
- API docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

## CLI examples

```bash
# Point the CLI at a local or deployed TPT service
export TPT_BASE_URL=http://localhost:8000
export TPT_API_KEY=your-read-write-admin-key

# Create a persona with useful attributes
tpt persona create user_123 \
  --name "Jane Doe" \
  -e role=product_manager \
  -e plan=enterprise \
  -e company="Acme Corp" \
  -e segment="workflow-heavy PMs"

# Add or update an entity
tpt entity set user_123 lifecycle_stage activated

# Track product behavior
tpt track user_123 page_view -p '{"page":"/personas","project":"pricing-redesign"}'
tpt track user_123 feature_used -p '{"feature":"dashboard_filter"}'

# Inspect a persona timeline
tpt events user_123

# Run and inspect clustering
tpt cluster run --algo kmeans
tpt cluster results
```

See [`examples/`](examples/) for runnable scripts.

## How it works

TPT runs as a standalone API service with a CLI client:

1. A client sends persona, event, or session data to the FastAPI API.
2. TPT stores that data in SQLite or Supabase/Postgres.
3. The dashboard, CLI, and API can inspect personas, timelines, sessions, logs, and clustering results.

```text
Client app / CLI ──▶ TPT API ──▶ SQLite or Supabase/Postgres
                         │
                         └──▶ Dashboard and API docs
```

Recommended identity convention: use a stable `distinct_id`, such as an internal user ID, account-user compound ID, or privacy-preserving hash.

### Environment variables

```dotenv
API_KEY=your-read-write-admin-key
WRITE_KEY=your-browser-safe-ingestion-key
DATABASE_MODE=sqlite
DATABASE_URL=postgresql+asyncpg://... # optional, for Supabase/Postgres mode
SUPABASE_URL=https://your-project.supabase.co # optional screenshot storage
SUPABASE_SERVICE_KEY=your-service-role-key # optional screenshot storage
DB_SCHEMA=public
```

## Browser event ingestion

Use `WRITE_KEY` for client-side ingestion. It can only create events and sessions; reading persona data still requires `API_KEY`.

```js
await fetch(`${TPT_BASE_URL}/api/v1/track?distinct_id=${encodeURIComponent(userId)}`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": TPT_WRITE_KEY,
  },
  body: JSON.stringify({
    event_type: "page_view",
    properties: {
      page: window.location.pathname,
      project_id: currentProjectId,
      source: "web-app",
    },
  }),
});
```

Optional screenshots can be passed as a base64 string in `screenshot`. When Supabase Storage is configured, TPT uploads and deduplicates screenshots; otherwise it stores a data URL fallback for local dashboard previews.

## Session replay ingestion

Create a session, then append rrweb event batches:

```js
const session = await fetch(`${TPT_BASE_URL}/api/v1/sessions`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": TPT_WRITE_KEY,
  },
  body: JSON.stringify({
    distinct_id: userId,
    url: window.location.href,
  }),
}).then((res) => res.json());

await fetch(`${TPT_BASE_URL}/api/v1/sessions/${session.id}/events`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": TPT_WRITE_KEY,
  },
  body: JSON.stringify(rrwebEvents),
});
```

Replay sessions from the dashboard or directly at:

```text
/replay/<session_id>
```

## API summary

All endpoints require `X-API-Key`. Event and session write endpoints accept either `API_KEY` or `WRITE_KEY`; read endpoints require `API_KEY`.

| Area | Method | Endpoint | Purpose |
| --- | --- | --- | --- |
| Personas | `POST` | `/api/v1/personas` | Create a persona |
| Personas | `GET` | `/api/v1/personas` | List/search personas |
| Personas | `GET` | `/api/v1/personas/{id}` | Get one persona with entities |
| Personas | `PATCH` | `/api/v1/personas/{id}` | Update persona metadata |
| Personas | `DELETE` | `/api/v1/personas/{id}` | Delete persona data |
| Entities | `POST` | `/api/v1/personas/{id}/entities` | Upsert persona entities |
| Entities | `GET` | `/api/v1/personas/{id}/entities` | List persona entities |
| Events | `POST` | `/api/v1/track?distinct_id=...` | Track an event and auto-create the persona if needed |
| Events | `GET` | `/api/v1/personas/{id}/events` | Read a persona timeline |
| Sessions | `POST` | `/api/v1/sessions` | Create an rrweb session |
| Sessions | `POST` | `/api/v1/sessions/{id}/events` | Append rrweb event batches |
| Sessions | `GET` | `/api/v1/sessions/{id}/events` | Read replay events |
| Logs | `GET` | `/api/v1/logs/stats` | Dashboard stats |
| Logs | `GET` | `/api/v1/logs/activity` | Recent product activity |
| Clusters | `POST` | `/api/v1/clusters/run` | Trigger cohort clustering |
| Clusters | `GET` | `/api/v1/clusters/latest` | Get latest cohort result |
| Clusters | `GET` | `/api/v1/clusters/runs` | List clustering runs |
| Clusters | `POST` | `/api/v1/clusters/schedule` | Schedule recurring clustering |

Full details are available in [`doc/API.md`](doc/API.md) and the live Swagger docs at `/docs`.

## Deployment notes

TPT is a standard ASGI app. The included `Procfile`, `Dockerfile`, and `nixpacks.toml` support common Railway-style deployments.

For production:

- Set `DATABASE_MODE=supabase` and provide `DATABASE_URL`.
- Set a strong `API_KEY` and separate `WRITE_KEY`.
- Configure `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` if you want screenshot storage.
- Keep `API_KEY` server-side only; expose only `WRITE_KEY` to browsers.
- If sharing a Supabase database across environments, set `DB_SCHEMA` (`public`, `staging`, `dev`, etc.).

## Documentation

- [Design & Architecture](doc/DESIGN.md)
- [Clustering Algorithms](doc/CLUSTERING.md)
- [API Reference](doc/API.md)
- [Roadmap](doc/ROADMAP.md)

## License

MIT
