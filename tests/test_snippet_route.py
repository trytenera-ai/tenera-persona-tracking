"""Browser snippet route and replay-safety checks."""

import os

os.environ["TPT_ACCESS_CODE"] = "teneraincadmin"

from fastapi.testclient import TestClient

from app.main import app


def test_tpt_js_served():
    with TestClient(app) as client:
        res = client.get("/tpt.js")

    assert res.status_code == 200
    assert "javascript" in res.headers["content-type"]
    body = res.text
    assert "__TPT_RECORDER_ACTIVE__" in body
    assert "firstFlushTimer = setTimeout" in body
    assert "flushReplayEvents(false)" in body
    assert "navigator.sendBeacon" not in body


def test_tpt_js_uses_keepalive_only_for_explicit_paths():
    with TestClient(app) as client:
        res = client.get("/tpt.js")

    body = res.text
    assert "flushReplayEvents(true)" in body
    assert "flushReplayEvents(false)" in body
    assert "keepalive: Boolean(keepalive)" in body
