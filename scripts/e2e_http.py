#!/usr/bin/env python3
# ruff: noqa: E501,N818
"""HTTP end-to-end smoke suite for Tenera Persona Tracking (TPT).

This script intentionally talks to TPT only through public HTTP endpoints, so it can
run against:

- a local SQLite-backed server
- a Railway deployment
- a Supabase/Postgres-backed deployment

Required env:
  TPT_E2E_API_KEY or API_KEY

Optional env:
  TPT_E2E_BASE_URL   default: http://localhost:8000
  TPT_E2E_WRITE_KEY  default: TPT_E2E_API_KEY/API_KEY
  TPT_E2E_ENV        production|staging; default inferred from BASE_URL
  TPT_E2E_KEEP_DATA  set to 1 to skip cleanup
  TPT_E2E_TIMEOUT    per-request timeout seconds; default 20
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


class E2EFailure(AssertionError):
    pass


@dataclass
class Config:
    base_url: str
    api_key: str
    write_key: str
    env: str
    timeout: float
    keep_data: bool


@dataclass
class CheckRecorder:
    checks: list[str] = field(default_factory=list)

    def ok(self, name: str, condition: bool, message: str) -> None:
        if not condition:
            raise E2EFailure(f"{name}: {message}")
        self.checks.append(name)

    def record(self, name: str) -> None:
        self.checks.append(name)

    @property
    def count(self) -> int:
        return len(self.checks)


def _env(name: str, fallback: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name) or fallback


def load_config() -> Config:
    base_url = (_env("TPT_E2E_BASE_URL", "http://localhost:8000") or "").rstrip("/")
    api_key = _env("TPT_E2E_API_KEY", _env("API_KEY"))
    if not api_key:
        raise E2EFailure("Set TPT_E2E_API_KEY or API_KEY before running the TPT e2e suite")
    write_key = _env("TPT_E2E_WRITE_KEY", _env("WRITE_KEY", api_key)) or api_key
    hostname = urllib.parse.urlparse(base_url).hostname or ""
    inferred_env = "production" if "production" in hostname else "staging"
    env = (_env("TPT_E2E_ENV", inferred_env) or inferred_env).lower()
    if env not in {"production", "staging"}:
        raise E2EFailure("TPT_E2E_ENV must be 'production' or 'staging'")
    timeout = float(_env("TPT_E2E_TIMEOUT", "20") or "20")
    keep_data = (_env("TPT_E2E_KEEP_DATA", "") or "").lower() in {"1", "true", "yes"}
    return Config(base_url=base_url, api_key=api_key, write_key=write_key, env=env, timeout=timeout, keep_data=keep_data)


class Client:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Any] = None,
        write: bool = False,
        api_key: Optional[str] = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        url = self.cfg.base_url + path
        data = None
        key = api_key if api_key is not None else (self.cfg.write_key if write else self.cfg.api_key)
        headers = {
            "X-API-Key": key,
            "Content-Type": "application/json",
            "User-Agent": "tpt-e2e/1.0",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                status = resp.status
                raw = resp.read()
                content_type = resp.headers.get("content-type", "")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.code not in expected:
                detail = raw.decode("utf-8", "replace")[:1000]
                raise E2EFailure(f"{method} {path} returned HTTP {exc.code}; expected {expected}. Body: {detail}") from exc
            return _decode_response(raw, exc.headers.get("content-type", ""), exc.code)
        except urllib.error.URLError as exc:
            raise E2EFailure(f"{method} {path} failed to connect to {url}: {exc}") from exc

        if status not in expected:
            detail = raw.decode("utf-8", "replace")[:1000]
            raise E2EFailure(f"{method} {path} returned HTTP {status}; expected {expected}. Body: {detail}")
        return _decode_response(raw, content_type, status)


def _decode_response(raw: bytes, content_type: str, status: int) -> Any:
    if status == 204 or not raw:
        return None
    if "application/json" in content_type:
        return json.loads(raw.decode("utf-8"))
    return raw.decode("utf-8", "replace")


def q(params: dict[str, Any]) -> str:
    return "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def find_personas(client: Client, search: str, *, env: Optional[str] = None, hide_anonymous: Optional[bool] = None, exclude_prefixes: Optional[str] = None, exclude_prefixes_logic: str = "or", limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"search": search, "limit": limit, "offset": offset}
    if env:
        params["env"] = env
    if hide_anonymous is not None:
        params["hide_anonymous"] = "true" if hide_anonymous else "false"
    if exclude_prefixes:
        params["exclude_prefixes"] = exclude_prefixes
        params["exclude_prefixes_logic"] = exclude_prefixes_logic
    data = client.request("GET", "/api/v1/personas" + q(params))
    if not (isinstance(data, dict) and isinstance(data.get("results"), list)):
        raise E2EFailure("personas list returned unexpected shape")
    return data["results"]


def cleanup(client: Client, run_id: str) -> None:
    # Search without env filtering so cleanup works even if URL/env inference changes.
    personas = find_personas(client, run_id)
    for persona in personas:
        pid = persona.get("id")
        if pid:
            client.request("DELETE", f"/api/v1/personas/{urllib.parse.quote(pid)}", expected=(204,))


def base_url_for_env(env: str, run_id: str, slug: str = "e2e") -> str:
    if env == "production":
        return f"https://tpt-e2e-production.example.com/{slug}/{run_id}"
    return f"https://staging.tpt-e2e.example.com/{slug}/{run_id}"


def tiny_jpeg_b64() -> str:
    # 1x1 jpeg; small enough to exercise screenshot storage without meaningful data.
    return "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/Aaf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/Aaf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/ISf/2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"


def main() -> int:
    cfg = load_config()
    client = Client(cfg)
    checks = CheckRecorder()
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    main_id = f"tpt-e2e-{run_id}"
    secondary_id = f"tpt-e2e-page-{run_id}"
    hidden_id = f"tpt-e2e-hidden-{run_id}"
    invited_id = f"invited-tpt-e2e-{run_id}"
    existing_id = f"existing-tpt-e2e-{run_id}"
    anon_id = f"anon_tpt_e2e_{run_id}"
    opposite_env = "production" if cfg.env == "staging" else "staging"
    opposite_id = f"tpt-e2e-{opposite_env}-{run_id}"
    base_app_url = base_url_for_env(cfg.env, run_id)
    opposite_app_url = base_url_for_env(opposite_env, run_id)

    print(f"TPT E2E start base_url={cfg.base_url} env={cfg.env} run_id={run_id}")

    try:
        health = client.request("GET", "/health")
        checks.ok("health endpoint", health.get("status") == "ok", f"unexpected response: {health}")

        dashboard = client.request("GET", "/")
        checks.ok("dashboard HTML renders", "Persona" in dashboard or "Tenera" in dashboard, "missing expected dashboard content")
        checks.ok("dashboard has no environment tab", "switchEnv" not in dashboard and "Environment" not in dashboard, "dashboard still appears to expose env switching UI")
        checks.ok("dashboard has default anonymous toggle", "hide-anonymous-toggle" in dashboard and "checked" in dashboard, "missing checked anonymous toggle")
        checks.ok("dashboard has browser-stored prefix filter", "tpt_dashboard_prefix_filters" in dashboard and "prefix-input" in dashboard, "missing persisted prefix filter UI")
        checks.ok("dashboard displays session count", "session-count" in dashboard and "/api/v1/sessions" in dashboard, "missing sessions summary integration")

        # Auth boundary checks.
        client.request("GET", "/api/v1/personas", api_key="wrong-tpt-e2e-key", expected=(401,))
        checks.record("read endpoint rejects wrong API key")
        client.request("POST", "/api/v1/track" + q({"distinct_id": main_id}), api_key="wrong-tpt-e2e-key", expected=(401,), body={"event_type": "bad_key"})
        checks.record("write endpoint rejects wrong API key")
        if cfg.write_key != cfg.api_key:
            client.request("GET", "/api/v1/personas", api_key=cfg.write_key, expected=(401,))
            checks.record("write key cannot read personas")

        created = client.request(
            "POST",
            "/api/v1/personas",
            body={
                "distinct_id": main_id,
                "name": "TPT E2E User",
                "description": "Synthetic user created by TPT HTTP e2e suite",
                "entities": [
                    {"key": "source", "value": "tpt-e2e"},
                    {"key": "run_id", "value": run_id},
                ],
            },
            expected=(201,),
        )
        persona_id = created["id"]
        checks.ok("persona create", created["distinct_id"] == main_id, "created persona distinct_id mismatch")

        duplicate = client.request("POST", "/api/v1/personas", body={"distinct_id": main_id}, expected=(409,))
        checks.ok("duplicate persona rejected", isinstance(duplicate, dict), "duplicate create did not return JSON error")

        patched = client.request("PATCH", f"/api/v1/personas/{persona_id}", body={"name": "TPT E2E User Updated", "description": "Updated by e2e"})
        checks.ok("persona update", patched.get("name") == "TPT E2E User Updated", "persona patch did not persist")

        listed = find_personas(client, main_id)
        checks.ok("persona search/list", any(p["id"] == persona_id for p in listed), "created persona was not returned by list/search")

        fetched = client.request("GET", f"/api/v1/personas/{persona_id}")
        checks.ok("persona get by ID", fetched["id"] == persona_id, "persona get returned wrong ID")

        entities = client.request(
            "POST",
            f"/api/v1/personas/{persona_id}/entities",
            body=[
                {"key": "plan", "value": "e2e-test"},
                {"key": "lifecycle_stage", "value": "daily-e2e"},
            ],
        )
        checks.ok("entity upsert", len(entities) >= 2, "entity upsert returned fewer entities than expected")
        entity_list = client.request("GET", f"/api/v1/personas/{persona_id}/entities")
        checks.ok("entity list", any(e.get("key") == "plan" for e in entity_list), "entity list missing plan")
        client.request("DELETE", f"/api/v1/personas/{persona_id}/entities/plan", expected=(204,))
        entity_list_after_delete = client.request("GET", f"/api/v1/personas/{persona_id}/entities")
        checks.ok("entity delete", not any(e.get("key") == "plan" for e in entity_list_after_delete), "deleted entity still present")

        # Additional personas for pagination/prefix/env/filter coverage.
        for did in [secondary_id, invited_id, existing_id]:
            client.request("POST", "/api/v1/personas", body={"distinct_id": did, "name": did, "entities": [{"key": "run_id", "value": run_id}]}, expected=(201,))
        checks.record("additional personas created for filters")

        page_one = find_personas(client, run_id, limit=1, offset=0)
        page_two = find_personas(client, run_id, limit=1, offset=1)
        checks.ok("persona pagination", len(page_one) == 1 and len(page_two) == 1 and page_one[0]["id"] != page_two[0]["id"], "limit/offset did not page distinct personas")

        tracked = client.request(
            "POST",
            "/api/v1/track" + q({"distinct_id": main_id}),
            write=True,
            expected=(201,),
            body={
                "event_type": "page_view",
                "screenshot": tiny_jpeg_b64(),
                "properties": {
                    "env": cfg.env,
                    "url": base_app_url,
                    "path": f"/e2e/{run_id}",
                    "run_id": run_id,
                    "source": "tpt-http-e2e",
                },
            },
        )
        checks.ok("event tracking through write key", tracked.get("event_type") == "page_view", "track response mismatch")
        checks.ok("screenshot capture persisted", bool(tracked.get("screenshot_url")), "tracked screenshot did not return a screenshot URL")

        client.request(
            "POST",
            "/api/v1/track" + q({"distinct_id": hidden_id}),
            write=True,
            expected=(201,),
            body={"event_type": "page_view", "properties": {"env": cfg.env, "url": base_url_for_env(cfg.env, run_id, "hidden"), "run_id": run_id}},
        )
        client.request(
            "POST",
            "/api/v1/track" + q({"distinct_id": anon_id}),
            write=True,
            expected=(201,),
            body={"event_type": "page_view", "properties": {"env": cfg.env, "url": base_url_for_env(cfg.env, run_id, "anon"), "run_id": run_id}},
        )
        client.request(
            "POST",
            "/api/v1/track" + q({"distinct_id": opposite_id}),
            write=True,
            expected=(201,),
            body={"event_type": "page_view", "properties": {"env": opposite_env, "url": opposite_app_url, "run_id": run_id}},
        )
        checks.record("event tracking creates hidden anonymous and opposite-env personas")

        events = client.request("GET", f"/api/v1/personas/{persona_id}/events" + q({"event_type": "page_view", "limit": 20}))
        checks.ok("persona event timeline", any(e.get("properties", {}).get("run_id") == run_id for e in events), "tracked page_view event was not returned")

        filtered_events = client.request("GET", f"/api/v1/personas/{persona_id}/events" + q({"event_type": "not_real", "limit": 20}))
        checks.ok("event type filter", filtered_events == [], "nonmatching event_type returned rows")

        session = client.request(
            "POST",
            "/api/v1/sessions",
            write=True,
            expected=(201,),
            body={"distinct_id": main_id, "url": base_url_for_env(cfg.env, run_id, "session")},
        )
        session_id = session["id"]
        checks.ok("session create", bool(session_id), "session create did not return ID")

        reused_session = client.request(
            "POST",
            "/api/v1/sessions",
            write=True,
            expected=(201,),
            body={"distinct_id": main_id, "url": base_url_for_env(cfg.env, run_id, "session-reload")},
        )
        checks.ok(
            "active session reuse",
            reused_session.get("id") == session_id and reused_session.get("reused") is True,
            "active same-user session was not reused",
        )

        now_ms = int(time.time() * 1000)
        rrweb_events = [
            {"type": 4, "timestamp": now_ms + 50, "data": {"href": base_url_for_env(cfg.env, run_id, "session"), "width": 1280, "height": 720}},
            {"type": 5, "timestamp": now_ms, "data": {"source": 2, "x": 10, "y": 20}},
        ]
        client.request("POST", f"/api/v1/sessions/{session_id}/events", write=True, expected=(204,), body=rrweb_events)
        replay = client.request("GET", f"/api/v1/sessions/{session_id}/events")
        replay_events = replay.get("events", [])
        checks.ok("session replay events append/read", len(replay_events) >= 2, "session replay events were not returned")
        checks.ok("session replay events sorted", replay_events[0].get("timestamp", 0) <= replay_events[-1].get("timestamp", 0), "session events were not sorted by timestamp")

        replay_html = client.request("GET", f"/replay/{session_id}")
        checks.ok("replay page renders", session_id in replay_html and "/api/v1/sessions/" in replay_html, "replay page missing session fetch wiring")

        sessions = client.request("GET", "/api/v1/sessions" + q({"env": cfg.env, "hide_anonymous": "true", "limit": 1}))
        checks.ok("sessions count endpoint", isinstance(sessions.get("count"), int) and sessions["count"] >= 1, "sessions count was missing or zero")
        sessions_opposite = client.request("GET", "/api/v1/sessions" + q({"env": opposite_env, "limit": 50}))
        checks.ok("session environment filter", not any(s.get("id") == session_id for s in sessions_opposite.get("results", [])), "opposite env sessions included current env session")

        stats = client.request("GET", "/api/v1/logs/stats" + q({"env": cfg.env, "hide_anonymous": "false"}))
        checks.ok("log stats include recent activity", stats.get("total_24h", 0) >= 1, "logs stats did not include recent e2e activity")
        stats_for_user = client.request("GET", "/api/v1/logs/stats" + q({"distinct_id": main_id, "env": cfg.env, "hide_anonymous": "false"}))
        checks.ok("log stats distinct_id filter", stats_for_user.get("total_24h", 0) >= 1, "distinct_id stats missing main user")

        activity = client.request("GET", "/api/v1/logs/activity" + q({"env": cfg.env, "limit": 500, "hide_anonymous": "false"}))
        run_activity = [r for r in activity if r.get("metadata", {}).get("run_id") == run_id]
        checks.ok("activity log includes test event", any(r.get("distinct_id") == main_id for r in run_activity), "logs activity did not include main e2e event")

        saved_false = client.request("GET", "/api/v1/logs/activity" + q({"saved": "false", "limit": 50}))
        checks.ok("saved=false activity filter", saved_false == [], "saved=false activity should be empty for persisted events")
        failed_events = client.request("GET", "/api/v1/logs/failed-events" + q({"limit": 50, "distinct_id": main_id}))
        checks.ok("failed-events endpoint", failed_events == [], "failed-events endpoint should return an empty list")

        visible_after_prefix = find_personas(client, run_id, env=cfg.env, exclude_prefixes="tpt-e2e-hidden-", exclude_prefixes_logic="or")
        checks.ok("prefix filter OR keeps main", any(p["distinct_id"] == main_id for p in visible_after_prefix), "prefix filter removed the main e2e persona unexpectedly")
        checks.ok("prefix filter OR hides hidden", not any(p["distinct_id"] == hidden_id for p in visible_after_prefix), "prefix filter did not hide hidden e2e persona")

        visible_after_multi_prefix = find_personas(client, run_id, exclude_prefixes="invited,existing", exclude_prefixes_logic="or")
        checks.ok("multi-prefix filter hides invited/existing", not any(p["distinct_id"] in {invited_id, existing_id} for p in visible_after_multi_prefix), "multi-prefix filter did not hide invited/existing test personas")

        and_logic = find_personas(client, run_id, exclude_prefixes="invited,existing", exclude_prefixes_logic="and")
        checks.ok("prefix filter AND is less aggressive", any(p["distinct_id"] in {invited_id, existing_id} for p in and_logic), "AND logic unexpectedly hid single-prefix personas")

        visible_without_anon = find_personas(client, run_id, env=cfg.env, hide_anonymous=True)
        checks.ok("hide_anonymous personas", not any(p["distinct_id"] == anon_id for p in visible_without_anon), "hide_anonymous did not hide anon persona")

        current_env_personas = find_personas(client, run_id, env=cfg.env, hide_anonymous=False)
        opposite_env_personas = find_personas(client, run_id, env=opposite_env, hide_anonymous=False)
        checks.ok("persona env filter includes current env", any(p["distinct_id"] == main_id for p in current_env_personas), "current env filter missed main persona")
        checks.ok("persona env filter excludes opposite env", not any(p["distinct_id"] == opposite_id for p in current_env_personas), "current env filter included opposite-env persona")
        checks.ok("persona env filter finds opposite env", any(p["distinct_id"] == opposite_id for p in opposite_env_personas), "opposite env filter missed opposite-env persona")

        production_activity = client.request("GET", "/api/v1/logs/activity" + q({"env": opposite_env, "limit": 500, "hide_anonymous": "false"}))
        checks.ok("activity env filter isolates opposite env", any(r.get("distinct_id") == opposite_id and r.get("metadata", {}).get("run_id") == run_id for r in production_activity), "opposite env activity missing expected row")
        checks.ok("activity env filter excludes current env", not any(r.get("distinct_id") == main_id and r.get("metadata", {}).get("run_id") == run_id for r in production_activity), "opposite env activity included current env row")

        client.request("GET", f"/api/v1/personas/not-a-real-persona-{run_id}", expected=(404,))
        checks.record("missing persona returns 404")
        client.request("GET", f"/api/v1/sessions/not-a-real-session-{run_id}/events", expected=(404,))
        checks.record("missing session returns 404")

        print(f"TPT E2E PASS ({checks.count} checks)")
        for name in checks.checks:
            print(f"  ✓ {name}")
        return 0
    finally:
        if cfg.keep_data:
            print(f"TPT_E2E_KEEP_DATA enabled; leaving run data in place for run_id={run_id}")
        else:
            try:
                cleanup(client, run_id)
                print("TPT E2E cleanup complete")
            except Exception as exc:  # noqa: BLE001 - cleanup should not mask earlier failure
                print(f"WARNING: cleanup failed for run_id={run_id}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except E2EFailure as exc:
        print(f"TPT E2E FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
