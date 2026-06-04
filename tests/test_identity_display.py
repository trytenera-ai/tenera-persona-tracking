from types import SimpleNamespace

from app.api.v1.logs import _persona_display_id


def test_persona_display_id_prefers_metadata_email_over_uuid():
    persona = SimpleNamespace(distinct_id="8f2efdae-6d98-49cf-bfda-58816e715f71", name=None, entities=[])

    assert _persona_display_id(persona, {"user_email": "customer@example.com"}) == "customer@example.com"


def test_persona_display_id_uses_email_entity_before_uuid():
    persona = SimpleNamespace(
        distinct_id="8f2efdae-6d98-49cf-bfda-58816e715f71",
        name=None,
        entities=[SimpleNamespace(key="email", value="customer@example.com")],
    )

    assert _persona_display_id(persona, {}) == "customer@example.com"


def test_persona_display_id_hides_uuid_when_no_email_exists():
    persona = SimpleNamespace(
        distinct_id="8f2efdae-6d98-49cf-bfda-58816e715f71",
        name=None,
        entities=[],
    )

    assert _persona_display_id(persona, {}) == "Unknown user"
