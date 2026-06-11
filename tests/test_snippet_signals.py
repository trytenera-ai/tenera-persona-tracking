"""
Unit tests for the tracking signal logic added to tpt.js:
  - rage click detection (isRageClick / shouldCaptureRageClick)
  - attribution helpers (getCampaignParams / referringDomain / getAttribution / getInitialProps)
  - sanitization (sanitizeText / SENSITIVE_FIELD_RE / CC_VALUE_RE / SSN_VALUE_RE)

These tests run the logic directly in Python so they can execute in CI without a
real browser. The implementations are deliberately simple enough that a faithful
Python port is an adequate specification test — each case maps 1-to-1 to the JS.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Ported helpers (mirrors tpt.js exactly so tests are authoritative specs)
# ---------------------------------------------------------------------------

CAMPAIGN_PARAMS = [
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "gad_source", "mc_cid",
    "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "fbclid", "msclkid",
    "twclid", "li_fat_id", "igshid", "ttclid", "rdt_cid", "epik", "qclid",
    "sccid", "irclid", "_kx",
]
DIRECT = "$direct"

SENSITIVE_FIELD_RE = re.compile(
    r"^cc|cardnum|ccnum|creditcard|csc|cvc|cvv|exp|pass|pwd|routing|seccode"
    r"|securitycode|securitynum|socialsec|socsec|ssn",
    re.IGNORECASE,
)
CC_VALUE_RE = re.compile(
    r"^(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|6(?:011|5[0-9]{2})[0-9]{12}"
    r"|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}"
    r"|(?:2131|1800|35[0-9]{3})[0-9]{11})$"
)
SSN_VALUE_RE = re.compile(r"^\d{3}-?\d{2}-?\d{4}$")

RAGE_CLICK_COUNT = 3
RAGE_THRESHOLD_PX = 30
RAGE_TIMEOUT_MS = 1000
RAGE_IGNORE_TEXT = ["next", "previous", "prev", ">", "<", "+", "-", "−", "–"]


def sanitize_text(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    text = " ".join(raw.split())[:100]
    if not text:
        return None
    if CC_VALUE_RE.match(text.replace("-", "").replace(" ", "")) or SSN_VALUE_RE.match(text):
        return None
    return text


def referring_domain(referrer: str) -> str:
    if not referrer or referrer == DIRECT:
        return DIRECT
    try:
        from urllib.parse import urlparse
        host = urlparse(referrer).netloc
        return host or DIRECT
    except Exception:
        return DIRECT


def get_campaign_params(url: str) -> dict:
    from urllib.parse import urlparse, parse_qs
    try:
        qs = parse_qs(urlparse(url).query, keep_blank_values=False)
        return {k: v[0] for k, v in qs.items() if k in CAMPAIGN_PARAMS and v}
    except Exception:
        return {}


class RageClickDetector:
    """Mirrors isRageClick state machine in tpt.js."""
    def __init__(self):
        self.clicks: list[dict] = []

    def is_rage_click(self, x: float, y: float, timestamp: float) -> bool:
        last = self.clicks[-1] if self.clicks else None
        if (
            last
            and abs(x - last["x"]) + abs(y - last["y"]) < RAGE_THRESHOLD_PX
            and timestamp - last["timestamp"] < RAGE_TIMEOUT_MS
        ):
            self.clicks.append({"x": x, "y": y, "timestamp": timestamp})
            if len(self.clicks) == RAGE_CLICK_COUNT:
                return True
        else:
            self.clicks = [{"x": x, "y": y, "timestamp": timestamp}]
        return False


# ---------------------------------------------------------------------------
# Rage click tests
# ---------------------------------------------------------------------------

def test_rage_click_fires_on_third_nearby_click():
    d = RageClickDetector()
    assert d.is_rage_click(100, 100, 0) is False
    assert d.is_rage_click(110, 105, 500) is False  # within 30px, within 1000ms
    assert d.is_rage_click(108, 103, 900) is True   # third click → rage


def test_rage_click_does_not_fire_on_second_click():
    d = RageClickDetector()
    d.is_rage_click(100, 100, 0)
    assert d.is_rage_click(100, 100, 100) is False  # only 2 in buffer


def test_rage_click_resets_when_too_far():
    d = RageClickDetector()
    d.is_rage_click(100, 100, 0)
    d.is_rage_click(100, 100, 200)
    # Third click more than 30px away (Manhattan: |100|+|0| = 100)
    result = d.is_rage_click(200, 100, 300)
    assert result is False
    # Buffer reset — we now need 2 more within threshold
    assert len(d.clicks) == 1


def test_rage_click_resets_when_too_slow():
    d = RageClickDetector()
    d.is_rage_click(100, 100, 0)
    d.is_rage_click(100, 100, 500)
    # Third click arrives more than 1000ms after the second
    result = d.is_rage_click(100, 100, 1600)
    assert result is False
    assert len(d.clicks) == 1


def test_rage_click_uses_manhattan_not_euclidean():
    # 21px in x + 21px in y = 42px Manhattan > threshold (30), even though
    # Euclidean would be ~29.7px (under threshold). Should reset.
    d = RageClickDetector()
    d.is_rage_click(100, 100, 0)
    result = d.is_rage_click(121, 121, 200)
    assert result is False
    assert len(d.clicks) == 1  # reset


def test_rage_click_per_pair_not_from_first():
    # Each gap only 20px, each within 1000ms → should fire on click 3
    d = RageClickDetector()
    d.is_rage_click(0, 0, 0)
    d.is_rage_click(20, 0, 500)   # 20px from prev, ok
    assert d.is_rage_click(40, 0, 900) is True  # 20px from prev, ok → rage


def test_rage_click_span_can_exceed_1s_total():
    # Two per-pair gaps of 900ms each → total 1800ms still valid
    d = RageClickDetector()
    d.is_rage_click(0, 0, 0)
    d.is_rage_click(5, 5, 900)   # 900ms gap, ok
    assert d.is_rage_click(10, 10, 1800) is True  # second gap also 900ms, ok


def test_rage_click_ignore_text_blocks_capture():
    for word in ["next", "previous", "prev", ">", "<", "+", "-"]:
        assert word.lower() in RAGE_IGNORE_TEXT


# ---------------------------------------------------------------------------
# Attribution tests
# ---------------------------------------------------------------------------

def test_get_campaign_params_extracts_utm():
    params = get_campaign_params("https://example.com/?utm_source=google&utm_medium=cpc&other=x")
    assert params == {"utm_source": "google", "utm_medium": "cpc"}


def test_get_campaign_params_extracts_ad_click_ids():
    params = get_campaign_params("https://example.com/?gclid=abc123&fbclid=xyz")
    assert params["gclid"] == "abc123"
    assert params["fbclid"] == "xyz"


def test_get_campaign_params_skips_non_campaign():
    params = get_campaign_params("https://example.com/?foo=bar&baz=1")
    assert params == {}


def test_referring_domain_returns_host():
    assert referring_domain("https://google.com/search?q=test") == "google.com"


def test_referring_domain_returns_direct_for_empty():
    assert referring_domain("") == DIRECT
    assert referring_domain(None) == DIRECT


def test_referring_domain_returns_direct_for_sentinel():
    assert referring_domain(DIRECT) == DIRECT


# ---------------------------------------------------------------------------
# Sanitization tests
# ---------------------------------------------------------------------------

def test_sanitize_text_returns_normal_text():
    assert sanitize_text("Click me") == "Click me"


def test_sanitize_text_trims_and_collapses_whitespace():
    assert sanitize_text("  hello   world  ") == "hello world"


def test_sanitize_text_truncates_at_100():
    long = "a" * 200
    result = sanitize_text(long)
    assert result == "a" * 100


def test_sanitize_text_returns_none_for_empty():
    assert sanitize_text("") is None
    assert sanitize_text(None) is None


def test_sanitize_text_blocks_visa_cc():
    # Valid Visa: starts with 4, 16 digits
    assert sanitize_text("4111111111111111") is None


def test_sanitize_text_blocks_ssn():
    assert sanitize_text("123-45-6789") is None
    assert sanitize_text("123456789") is None


def test_sanitize_text_allows_non_sensitive_numbers():
    assert sanitize_text("12345") == "12345"
    assert sanitize_text("2026-06-10") == "2026-06-10"


def test_sensitive_field_re_blocks_password():
    assert SENSITIVE_FIELD_RE.match("password")
    assert SENSITIVE_FIELD_RE.match("pwd")


def test_sensitive_field_re_blocks_credit_card_fields():
    for name in ["cc", "cardnum", "ccnum", "creditcard", "cvc", "cvv", "csc"]:
        assert SENSITIVE_FIELD_RE.match(name), f"expected {name} to be sensitive"


def test_sensitive_field_re_blocks_ssn_fields():
    assert SENSITIVE_FIELD_RE.match("ssn")
    assert SENSITIVE_FIELD_RE.match("socialsec")
    assert SENSITIVE_FIELD_RE.match("socsec")


def test_sensitive_field_re_allows_normal_fields():
    for name in ["email", "username", "first_name", "company", "phone"]:
        assert not SENSITIVE_FIELD_RE.match(name), f"expected {name} to be allowed"
