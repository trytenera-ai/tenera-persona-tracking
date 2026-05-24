# Anonymous Session Support (TPT-native)

**Date:** 2026-05-23
**Status:** Approved design — ready for implementation plan
**Scope:** Sub-project 1 of 3 in the "Tenera analytics" initiative
**Primary repo:** `tenera-persona-tracking` (TPT). Thin companion change in the main Tenera app.

---

## Context

Tenera's product analytics pipeline has two systems:

1. **TPT** (`tenera-persona-tracking`) — a FastAPI service on Railway with its own Postgres/Supabase. It is the **raw data layer**: it ingests sessions, action-log events, and rrweb recordings, keyed by a per-user `personas.distinct_id`. It also currently has a built-in APScheduler clustering engine (see "Out of scope" below).
2. **Main Tenera app** — sends rrweb + `page_view` events to TPT via `src/components/TeneraTrackingProvider.tsx`.

### Repo boundary (locked for the whole initiative)

| Concern | Repo |
|---|---|
| Raw data collection — SDK, ingestion endpoints, sessions/events/rrweb storage | **TPT** |
| Anonymous → signed-in conversion (device-id, identify/merge) | **TPT** |
| "Tenera analytics" — raw-data processing + post-processing (daily/manual persona synthesis) | **Main Tenera app, on Modal** |
| First-party ("P1") persona injection into the dashboard | **Main Tenera app** |

This spec covers only **anonymous session support**, which lives entirely in TPT plus a thin client change.

### Problem

Anonymous (logged-out) traffic is captured **nowhere**:

- `TeneraTrackingProvider` returns early unless `isSignedIn && distinctId` (distinctId = Clerk email or user id). Logged-out visitors are never recorded.
- TPT's `personas.distinct_id` is `NOT NULL` and `UNIQUE` — there is no representation for an anonymous visitor.

We want logged-out visitors tracked with the **same fidelity** as logged-in users (rrweb session replay + events), a **persistent device identity** that survives across visits, and a **merge on sign-in** so one human becomes one persona over time (PostHog-style identify/alias).

---

## Decisions (confirmed)

- **"P1 persona" = first-party persona** (`sourceParty = '1p'`), i.e. derived from first-party product usage — not a priority tier. (Relevant to later sub-projects, recorded here for continuity.)
- **Anonymous identity:** persistent device ID in `localStorage`, used as `distinct_id` while logged out, **merged into the real persona on sign-in**.
- **Anonymous capture fidelity:** full — rrweb session replay + events, same as logged-in.
- **Identity representation (Approach A):** keep the existing single `distinct_id` column; anonymous personas use `distinct_id = "anon_<uuid4>"` plus an `is_anonymous` flag. (Rejected: a separate nullable `anonymous_id` column, and a dedicated `identities` mapping table — both more surface area than the current need. We can graduate to an `identities` table later if multi-device stitching is required.)
- **SDK delivery:** TPT **builds and serves a framework-agnostic browser bundle** (`/tpt.js`, PostHog-style script tag). All anonymous-lifecycle logic lives in TPT; the Tenera app becomes a thin consumer.

---

## Architecture & data flow

```
Logged-out visitor
  → loads /tpt.js from TPT, tpt.init({ apiHost, writeKey })
  → device id "anon_<uuid>" read/created in localStorage
  → POST /api/v1/sessions, /sessions/{id}/events (rrweb), /track   (distinct_id = anon id)

On sign-in (consumer calls tpt.identify(email)):
  → POST /api/v1/identify { anon_id, distinct_id }
  → TPT reassigns the anon persona's events/sessions/entities to the real persona, deletes the anon row
  → SDK switches active distinct_id to the real id and starts a fresh session
```

No new infrastructure. The same ingestion endpoints handle anonymous traffic; the only structurally new endpoint is `/identify`.

---

## Component 1 — TPT browser SDK (new, served by TPT)

A small bundle served at `GET /tpt.js`. Public API (PostHog-shaped):

- `tpt.init({ apiHost, writeKey })` — read/create persistent `tpt_anon_id` (uuid4) in `localStorage`; start a session; begin rrweb recording.
- `tpt.capture(event, properties)` — action logs / page views.
- `tpt.identify(distinctId)` — `POST /api/v1/identify`, then switch the active distinct_id and start a fresh session.

Responsibilities owned **here, once**:

- The `anon_<uuid>` convention and device-id persistence.
- Session creation + rrweb wiring: dynamic/lazy import of rrweb (its own chunk so the initial `tpt.js` stays light), event buffering, periodic flush (existing 5s cadence), final flush on unload.
- The identify lifecycle (anon → known transition; idempotent).
- Privacy defaults unchanged from today: `maskAllInputs: true`, `blockClass: "ph-no-capture"`, `maskTextClass: "ph-mask"`, `ignoreClass: "ph-ignore-input"`.

**Auth:** the SDK uses the **write key** (`X-API-Key`), which is safe to embed in browser JS and already only grants access to ingestion + identify.

---

## Component 2 — SDK build & serve (new mechanics in a Python repo)

TPT is Python/FastAPI; introduce a self-contained JS build that does not pollute the Railway/nixpacks Python image:

- Source in `client/src/*.ts`.
- An **esbuild** step bundles to `app/static/tpt.js` (+ a lazily-loaded rrweb chunk).
- **Commit the built artifact** (`app/static/tpt.js`) so the Python deploy serves it directly with no Node toolchain at runtime.
- A **CI step** (GitHub Action) rebuilds the bundle and **fails if the committed artifact is stale**, guaranteeing the served bundle matches `client/src`.
- FastAPI serves `/tpt.js` with cache headers; embed a `TPT_SDK_VERSION` constant for debugging.

---

## Component 3 — TPT server changes

- **Schema (Alembic migration):** add `personas.is_anonymous BOOLEAN NOT NULL DEFAULT false`, indexed. `distinct_id` stays `NOT NULL` / `UNIQUE`.
- **Auto-flag on ingest:** in the existing get-or-create-persona path used by `/track` and `/sessions`, set `is_anonymous = true` when a **newly created** persona's `distinct_id` starts with `anon_`. No request-shape change to those endpoints.
- **New `POST /api/v1/identify`** (accepts write key). Body `{ anon_id, distinct_id }`:
  1. Look up the anon persona by `anon_id`. If absent → ensure the real persona exists; return `{ merged: false }` (idempotent no-op).
  2. Get-or-create the real persona by `distinct_id` (`is_anonymous = false`).
  3. Reassign the anon persona's `events`, `sessions`, and `entities` to the real persona. On `entities` UNIQUE(persona_id, key) conflict → **real persona's value wins**; drop the anon duplicate.
  4. Delete the anon persona.
  - Idempotent and safe under concurrent tabs (a second call finds no anon row → no-op).

---

## Component 4 — Main Tenera app (thin consumer)

`src/components/TeneraTrackingProvider.tsx` drops all rrweb/fetch/buffer code and becomes:

- Load `tpt.js` via Next `<Script>` from `NEXT_PUBLIC_TPT_URL`; call `tpt.init({ apiHost: NEXT_PUBLIC_TPT_URL, writeKey: NEXT_PUBLIC_TPT_WRITE_KEY })`.
- Call `tpt.capture('page_view', { page })` on `pathname` change.
- Call `tpt.identify(email ?? userId)` when Clerk `isSignedIn` flips true.

No anonymous logic remains in the app.

---

## Component 5 — Dashboard

Show an **"anonymous" badge** on `is_anonymous` personas in the Personas tab so `anon_<uuid>` rows are legible. Small, optional-but-included.

---

## Error handling & edge cases

- **localStorage blocked** (privacy mode / SSR) → fall back to an in-memory ephemeral id; tracking still works for that page lifetime.
- **Login mid-session** → the in-progress anon rrweb session is reassigned by `/identify` (merge is by `persona_id`), so no events are lost; the SDK then starts a fresh session under the real id.
- **Repeat identify / multiple tabs** → idempotent no-op (second call finds no anon row).
- **Returning visitor who logs out then back in** → `anon_id` persists in localStorage; a later login simply re-merges. Expected.
- **Privacy** unchanged: anon rrweb uses the same masking/block defaults as today.

---

## Testing

- **Server (pytest, SQLite mode):**
  - `/identify` merge correctness — events, sessions, and entities reassigned from anon to real persona; real-wins on entity-key conflict; anon row deleted.
  - Idempotency — a second `/identify` call is a no-op.
  - Auto-flag — a newly created `anon_`-prefixed persona gets `is_anonymous = true`; a normal distinct_id does not.
- **SDK (unit):** pure helpers in isolation — anon-id get/create, identify state transition, batch flush.
- **Golden path (manual, per repo verification rule):**
  - Logged-out load → an `anon_<uuid>` persona with `page_view` events + an rrweb session appears in the TPT dashboard.
  - Sign in → the anon persona disappears and its events/session now sit under the email persona.

---

## Out of scope (this sub-project)

- TPT's existing APScheduler **clustering** engine. Under the locked repo boundary, "Tenera analytics" post-processing moves to the main app on Modal, so TPT's built-in clustering is legacy/unused for our purposes. Leaving it in place for now; potential cleanup later.
- The raw → "Tenera analytics" post-processing pipeline (sub-project 2) and first-party persona injection into the dashboard (sub-project 3). Each gets its own spec → plan → PR.
- Multi-device / multi-email identity stitching (would motivate a future `identities` mapping table).
