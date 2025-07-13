import pytest

from filesystem_provider import ID3TagRegistry


@pytest.fixture
def registry():
    reg = ID3TagRegistry()
    reg.register("POPM:test@example.com", tag_key="TEST", player_name="TestPlayer")
    reg.register("POPM:abc@example.com", tag_key="TEST1", player_name="FancyPlayer")
    reg.register("POPM:def@example.com", tag_key="UNKNOWN5")
    reg.register("POPM:example@domain.com", tag_key="ROUNDTRIP", player_name="Foobar")
    return reg


def test_known_keys_returns_expected_keys(registry):
    keys = registry.known_keys()
    assert all(k in keys for k in ["TEST", "TEST1", "UNKNOWN5", "ROUNDTRIP"])


@pytest.mark.parametrize(
    "lookup, expected",
    [
        ("TEST", "POPM:test@example.com"),
        ("TEST1", "POPM:abc@example.com"),
        ("UNKNOWN5", "POPM:def@example.com"),
        ("ROUNDTRIP", "POPM:example@domain.com"),
        ("NONEXISTENT", None),
    ],
)
def test_get_id3_tag_for_key(registry, lookup, expected):
    assert registry.get_id3_tag_for_key(lookup) == expected


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("POPM:test@example.com", "TEST"),
        ("POPM:abc@example.com", "TEST1"),
        ("POPM:def@example.com", "UNKNOWN5"),
        ("POPM:example@domain.com", "ROUNDTRIP"),
        ("POPM:nonexistent@example.com", None),
    ],
)
def test_get_key_for_id3_tag(registry, tag, expected):
    assert registry.get_key_for_id3_tag(tag) == expected


@pytest.mark.parametrize(
    "key, expected",
    [
        ("TEST", "TestPlayer"),
        ("TEST1", "FancyPlayer"),
        ("UNKNOWN5", "POPM:def@example.com"),
        ("ROUNDTRIP", "Foobar"),
        ("NONEXISTENT", "NONEXISTENT"),
    ],
)
def test_get_display_name_for_key(registry, key, expected):
    assert registry.display_name(key) == expected


@pytest.mark.parametrize(
    "tag_key, expected",
    [
        ("TEST", "TEST"),
        ("NONEXISTENT", "NONEXISTENT"),
        ("UNKNOWN5", "POPM:def@example.com"),
    ],
)
def test_get_config_value_variants(registry, tag_key, expected):
    assert registry.get_config_value(tag_key) == expected


def test_round_trip_key_to_tag_to_key(registry):
    key = "ROUNDTRIP"
    tag = registry.get_id3_tag_for_key(key)
    resolved_key = registry.get_key_for_id3_tag(tag)
    assert resolved_key == key


@pytest.mark.parametrize(
    "player_name, expected",
    [
        ("TestPlayer", "TEST"),
        ("FancyPlayer", "TEST1"),
        ("Foobar", "ROUNDTRIP"),
        ("POPM:def@example.com", "UNKNOWN5"),  # fallback player name
        ("not_registered", None),
    ],
)
def test_get_key_for_player_name(registry, player_name, expected):
    assert registry.get_key_for_player_name(player_name) == expected


@pytest.mark.parametrize(
    "input_value, expected",
    [
        ("TEST", "TEST"),
        ("POPM:test@example.com", "TEST"),
        ("POPM:abc@example.com", "TEST1"),
        ("UNKNOWNKEY", None),
        ("POPM:untracked@example.com", None),
    ],
)
def test_resolve_key_from_input(registry, input_value, expected):
    assert registry.resolve_key_from_input(input_value) == expected


@pytest.mark.parametrize(
    "tag_key, tag_value, expected_email",
    [
        ("TEST", "POPM:test@example.com", "test@example.com"),
        ("TEST1", "POPM:abc@example.com", "abc@example.com"),
        ("UNKNOWN5", "POPM:def@example.com", "def@example.com"),
        ("ROUNDTRIP", "POPM:example@domain.com", "example@domain.com"),
        ("TEXTKEY", "TXXX:RATING", None),
        ("NONEXISTENT", None, None),
    ],
)
def test_get_popm_email_for_key(registry, tag_key, tag_value, expected_email):
    assert registry.get_popm_email_for_key(tag_key) == expected_email


@pytest.mark.parametrize(
    "input_fn, arg",
    [
        (lambda r: r.get_id3_tag_for_key, None),
        (lambda r: r.get_id3_tag_for_key, ""),
        (lambda r: r.get_key_for_id3_tag, None),
        (lambda r: r.get_key_for_id3_tag, ""),
        (lambda r: r.get_key_for_player_name, None),
        (lambda r: r.get_key_for_player_name, ""),
        (lambda r: r.get_popm_email_for_key, None),
        (lambda r: r.get_popm_email_for_key, ""),
    ],
)
def test_registry_methods_with_empty_input(registry, input_fn, arg):
    assert input_fn(registry)(arg) is None


def test_register_existing_id3_tag_returns_existing_key(registry):
    assert registry.register("POPM:test@example.com") == "TEST"
