#!/usr/bin/env python3
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
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
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


def _env(name: str, fallback: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name) or fallback


def load_config() -> Config:
    base_url = (_env("TPT_E2E_BASE_URL", "http://localhost:8000") or "").rstrip("/")
    api_key = _env("TPT_E2E_API_KEY", _env("API_KEY"))
    if not api_key:
        raise E2EFailure("Set TPT_E2E_API_KEY or API_KEY before running the TPT e2e suite")
    write_key = _env("TPT_E2E_WRITE_KEY", _env("WRITE_KEY", api_key)) or api_key
    inferred_env = "production" if "production" in urllib.parse.urlparse(base_url).hostname or "" else "staging"
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
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        url = self.cfg.base_url + path
        data = None
        headers = {
            "X-API-Key": self.cfg.write_key if write else self.cfg.api_key,
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
            detail = raw.decode("utf-8", "replace")[:1000]
            raise E2EFailure(f"{method} {path} returned HTTP {exc.code}; expected {expected}. Body: {detail}") from exc
        except urllib.error.URLError as exc:
            raise E2EFailure(f"{method} {path} failed to connect to {url}: {exc}") from exc

        if status not in expected:
            detail = raw.decode("utf-8", "replace")[:1000]
            raise E2EFailure(f"{method} {path} returned HTTP {status}; expected {expected}. Body: {detail}")
        if status == 204 or not raw:
            return None
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8"))
        return raw.decode("utf-8", "replace")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise E2EFailure(message)


def q(params: dict[str, Any]) -> str:
    return "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def find_personas(client: Client, search: str, *, env: Optional[str] = None, hide_anonymous: Optional[bool] = None, exclude_prefixes: Optional[str] = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"search": search, "limit": 200}
    if env:
        params["env"] = env
    if hide_anonymous is not None:
        params["hide_anonymous"] = "true" if hide_anonymous else "false"
    if exclude_prefixes:
        params["exclude_prefixes"] = exclude_prefixes
        params["exclude_prefixes_logic"] = "or"
    data = client.request("GET", "/api/v1/personas" + q(params))
    assert_true(isinstance(data, dict) and isinstance(data.get("results"), list), "personas list returned unexpected shape")
    return data["results"]


def cleanup(client: Client, run_id: str) -> None:
    # Search without env filtering so cleanup works even if URL/env inference changes.
    personas = find_personas(client, run_id)
    for persona in personas:
        pid = persona.get("id")
        if pid:
            client.request("DELETE", f"/api/v1/personas/{urllib.parse.quote(pid)}", expected=(204,))


def main() -> int:
    cfg = load_config()
    client = Client(cfg)
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    main_id = f"tpt-e2e-{run_id}"
    hidden_id = f"tpt-e2e-hidden-{run_id}"
    anon_id = f"anon_tpt_e2e_{run_id}"
    base_app_url = "https://tpt-e2e-production.example.com" if cfg.env == "production" else "https://staging.tpt-e2e.example.com"

    print(f"TPT E2E start base_url={cfg.base_url} env={cfg.env} run_id={run_id}")

    try:
        health = client.request("GET", "/health")
        assert_true(health.get("status") == "ok", f"health returned unexpected response: {health}")

        dashboard = client.request("GET", "/")
        assert_true("Persona" in dashboard or "Tenera" in dashboard, "dashboard HTML did not render expected content")

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
        assert_true(created["distinct_id"] == main_id, "created persona distinct_id mismatch")

        listed = find_personas(client, main_id)
        assert_true(any(p["id"] == persona_id for p in listed), "created persona was not returned by list/search")

        fetched = client.request("GET", f"/api/v1/personas/{persona_id}")
        assert_true(fetched["id"] == persona_id, "persona get returned wrong ID")

        entities = client.request(
            "POST",
            f"/api/v1/personas/{persona_id}/entities",
            body=[
                {"key": "plan", "value": "e2e-test"},
                {"key": "lifecycle_stage", "value": "daily-e2e"},
            ],
        )
        assert_true(len(entities) >= 2, "entity upsert returned fewer entities than expected")

        client.request(
            "POST",
            "/api/v1/track" + q({"distinct_id": main_id}),
            write=True,
            expected=(201,),
            body={
                "event_type": "page_view",
                "properties": {
                    "env": cfg.env,
                    "url": f"{base_app_url}/e2e/{run_id}",
                    "path": f"/e2e/{run_id}",
                    "run_id": run_id,
                    "source": "tpt-http-e2e",
                },
            },
        )
        client.request(
            "POST",
            "/api/v1/track" + q({"distinct_id": hidden_id}),
            write=True,
            expected=(201,),
            body={"event_type": "page_view", "properties": {"env": cfg.env, "url": f"{base_app_url}/hidden/{run_id}", "run_id": run_id}},
        )
        client.request(
            "POST",
            "/api/v1/track" + q({"distinct_id": anon_id}),
            write=True,
            expected=(201,),
            body={"event_type": "page_view", "properties": {"env": cfg.env, "url": f"{base_app_url}/anon/{run_id}", "run_id": run_id}},
        )

        events = client.request("GET", f"/api/v1/personas/{persona_id}/events" + q({"event_type": "page_view", "limit": 20}))
        assert_true(any(e.get("properties", {}).get("run_id") == run_id for e in events), "tracked page_view event was not returned")

        session = client.request(
            "POST",
            "/api/v1/sessions",
            write=True,
            expected=(201,),
            body={"distinct_id": main_id, "url": f"{base_app_url}/session/{run_id}"},
        )
        session_id = session["id"]
        now_ms = int(time.time() * 1000)
        rrweb_events = [
            {"type": 4, "timestamp": now_ms, "data": {"href": f"{base_app_url}/session/{run_id}", "width": 1280, "height": 720}},
            {"type": 5, "timestamp": now_ms + 50, "data": {"source": 2, "x": 10, "y": 20}},
        ]
        client.request("POST", f"/api/v1/sessions/{session_id}/events", write=True, expected=(204,), body=rrweb_events)
        replay = client.request("GET", f"/api/v1/sessions/{session_id}/events")
        assert_true(len(replay.get("events", [])) >= 2, "session replay events were not returned")

        sessions = client.request("GET", "/api/v1/sessions" + q({"env": cfg.env, "hide_anonymous": "true", "limit": 1}))
        assert_true(isinstance(sessions.get("count"), int) and sessions["count"] >= 1, "sessions count was missing or zero")

        stats = client.request("GET", "/api/v1/logs/stats" + q({"env": cfg.env, "hide_anonymous": "false"}))
        assert_true(stats.get("total_24h", 0) >= 1, "logs stats did not include recent e2e activity")

        activity = client.request("GET", "/api/v1/logs/activity" + q({"env": cfg.env, "limit": 500, "hide_anonymous": "false"}))
        run_activity = [r for r in activity if r.get("metadata", {}).get("run_id") == run_id]
        assert_true(any(r.get("distinct_id") == main_id for r in run_activity), "logs activity did not include main e2e event")

        visible_after_prefix = find_personas(client, run_id, env=cfg.env, exclude_prefixes="tpt-e2e-hidden-")
        assert_true(any(p["distinct_id"] == main_id for p in visible_after_prefix), "prefix filter removed the main e2e persona unexpectedly")
        assert_true(not any(p["distinct_id"] == hidden_id for p in visible_after_prefix), "prefix filter did not hide hidden e2e persona")

        visible_without_anon = find_personas(client, run_id, env=cfg.env, hide_anonymous=True)
        assert_true(not any(p["distinct_id"] == anon_id for p in visible_without_anon), "hide_anonymous did not hide anon persona")

        print("TPT E2E PASS")
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
