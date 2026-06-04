from app.api.v1.events import IdentifyRequest, _profile_properties


def test_identify_profile_properties_are_flattened_and_include_email():
    body = IdentifyRequest(
        anon_id="anon_123",
        distinct_id="customer@example.com",
        properties={"name": "Jane Doe", "empty": "", "nested": {"no": True}},
    )

    assert _profile_properties(body) == {"name": "Jane Doe", "email": "customer@example.com"}
