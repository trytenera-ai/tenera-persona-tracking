# TPT HTTP E2E Suite

TPT includes a small HTTP-only end-to-end suite for daily smoke testing. It is designed to work against both local development and deployed services, including Supabase/Postgres-backed Railway deployments.

The suite intentionally does **not** connect to the database directly. It validates TPT through public HTTP endpoints only, which makes it suitable for deployments where the backing store is SQLite, Supabase/Postgres, or Railway-provisioned Postgres.

## What it covers

`scripts/e2e_http.py` validates:

- health endpoint
- dashboard HTML render and dashboard wiring for sessions, anonymous hiding, and browser-stored prefix filters
- read/write API-key rejection checks, including write-key read isolation when a separate write key is configured
- persona create/list/get/update/delete
- duplicate persona conflict handling
- persona pagination with `limit`/`offset`
- entity upsert/list/delete
- event tracking through the write key
- screenshot capture/storage response behavior
- persona event timeline reads and event-type filtering
- session creation
- rrweb session event append/read and timestamp ordering
- replay page render and API wiring
- session count endpoint and session environment filtering
- log stats, distinct-user stats, activity, `saved=false`, and failed-event endpoints
- production/staging environment isolation for personas and activity logs
- anonymous filtering
- OR and AND prefix filtering
- 404 handling for missing personas and sessions
- cleanup of synthetic personas/events/sessions

All test records use a unique `tpt-e2e-*` run ID and are deleted at the end by default.

## Run against local SQLite

From the repository root:

```bash
scripts/e2e_local.sh
```

This starts a temporary local SQLite-backed TPT server on `127.0.0.1:${TPT_E2E_PORT:-8765}`, runs the HTTP e2e suite, then tears everything down.

## Run against a deployed service

```bash
TPT_E2E_BASE_URL=https://tenera-persona-tracking-staging.up.railway.app \
TPT_E2E_API_KEY=... \
TPT_E2E_WRITE_KEY=... \
TPT_E2E_ENV=staging \
python3 scripts/e2e_http.py
```

For production:

```bash
TPT_E2E_BASE_URL=https://tenera-persona-tracking-production.up.railway.app \
TPT_E2E_API_KEY=... \
TPT_E2E_WRITE_KEY=... \
TPT_E2E_ENV=production \
python3 scripts/e2e_http.py
```

If `TPT_E2E_WRITE_KEY` is omitted, the suite falls back to `TPT_E2E_API_KEY` because TPT's write endpoints accept the full API key.

## Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TPT_E2E_BASE_URL` | No | `http://localhost:8000` | Target TPT service URL. |
| `TPT_E2E_API_KEY` | Yes | `API_KEY` | Full read/write API key for read endpoints and cleanup. |
| `TPT_E2E_WRITE_KEY` | No | `WRITE_KEY` or API key | Write key for event/session ingestion. |
| `TPT_E2E_ENV` | No | inferred from URL | `staging` or `production`; used by dashboard-style filters. |
| `TPT_E2E_TIMEOUT` | No | `20` | Per-request timeout in seconds. |
| `TPT_E2E_KEEP_DATA` | No | unset | Set to `1` to skip cleanup for debugging. |
| `TPT_E2E_PORT` | No | `8765` | Local server port used by `scripts/e2e_local.sh`. |

## Daily suite integration

A daily runner can call the deployed-service command above after resolving the deployment URL and secrets. The script exits non-zero on failure and prints `TPT E2E PASS (<n> checks)` on success.

Recommended daily target order:

1. staging deployment
2. production deployment only when explicitly desired
3. local SQLite smoke test in CI for PR-level checks

## Safety notes

- The suite creates only synthetic `tpt-e2e-*` personas and an `anon_tpt_e2e_*` persona.
- Cleanup deletes personas matching the unique run ID, which cascades associated entities/events/sessions.
- Set `TPT_E2E_KEEP_DATA=1` only when debugging a failure.
- Do not run against customer production data unless the target API key is intended for e2e writes and cleanup.
