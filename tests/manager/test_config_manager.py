from unittest.mock import MagicMock, patch

import pytest

from manager.config_manager import ConflictResolutionStrategy, LogLevel, PlayerType, TagWriteStrategy


@pytest.fixture(autouse=True)
def initialize_manager(monkeypatch, tmp_path):
    # supress conftest intitilization of manager
    pass


@pytest.fixture(autouse=True)
def ConfigManager(monkeypatch):
    monkeypatch.setattr("manager.config_manager.ConfigManager.CONFIG_FILE", "./does_not_exist.ini")
    from manager.config_manager import ConfigManager

    return ConfigManager


@pytest.fixture
def config_manager(request, monkeypatch, ConfigManager):
    """Fixture to provide a ConfigManager instance with CLI args set via rational defaults, overridden by request.param as a dict.
    Boolean keys: if True, add as --key (no value); if False/None, remove from defaults; else --key value.
    """
    default_args = {
        "source": "plex",
        "destination": "filesystem",
        "sync": "tracks",
        "log": "debug",
        "cache-mode": "metadata",
        "tag-write-strategy": "write_default",
        "default-tag": "MEDIAMONKEY",
        "conflict-resolution-strategy": "highest",
        "dry": False,  # include dry as a default for logic
    }
    override_args = dict(getattr(request, "param", {}))
    # Remove any default key if explicitly set to False/None
    merged_args = {**default_args, **override_args}
    argv = ["sync_ratings.py"]
    for k, v in merged_args.items():
        if v is None or v is False:
            continue
        argv.append(f"--{k}")
        argv.append(v) if v is not True else None
    monkeypatch.setattr("sys.argv", argv)
    cm = ConfigManager()
    return cm


@pytest.fixture
def mock_updater(request):
    """
    Fixture to create a mock ConfigUpdater, parameterized by a list of section setups.
    Each section setup is a dict: {name: str, has_key: bool, current_value: str|None}
    If request.param is not a list, fallback to old behavior for backward compatibility.
    """
    param = getattr(request, "param", None)
    sections = {}

    def make_section(name: str, has_key: bool, current_value: str | None):
        option = MagicMock(value=current_value) if has_key else None
        sect = MagicMock(spec_set=["name", "has_option", "get", "__setitem__"])
        sect.name = name
        sect.has_option.return_value = has_key
        sect.get.return_value = option
        sect.__setitem__ = MagicMock()
        return sect

    # Support new param style: list of dicts for section setup
    if isinstance(param, list):
        for section in param:
            name = section["name"]
            has_key = section.get("has_key", False)
            current_value = section.get("current_value", None)
            sections[name] = make_section(name, has_key, current_value)
    elif param is not None:
        # Fallback: old behavior for single section_name
        sections[param] = make_section(param, False, None)

    mock_updater = MagicMock()
    mock_updater.read = MagicMock()
    mock_updater.update_file = MagicMock()
    mock_updater.add_section = MagicMock(side_effect=lambda name: sections.setdefault(name, make_section(name, False, None)))
    mock_updater.has_section.side_effect = lambda name: name in sections
    mock_updater.__getitem__.side_effect = lambda name: sections.setdefault(name, make_section(name, False, None))
    mock_updater.iter_sections.side_effect = lambda: list(sections.values())
    mock_updater.sections = sections
    return mock_updater


class TestConfigEnum:
    def test_enum_coercion_source_destination(self, config_manager):
        assert config_manager.source == PlayerType.PLEX
        assert config_manager.destination == PlayerType.FILESYSTEM

    def test_enum_coercion_tag_write_strategy(self, config_manager):
        assert config_manager.tag_write_strategy == TagWriteStrategy.WRITE_DEFAULT

    def test_enum_coercion_conflict_resolution_strategy(self, config_manager):
        assert config_manager.conflict_resolution_strategy == ConflictResolutionStrategy.HIGHEST

    def test_enum_coercion_log_level(self, config_manager):
        assert config_manager.log == LogLevel.DEBUG

    @pytest.mark.parametrize(
        "enum_member,other,expected",
        [
            (TagWriteStrategy.WRITE_ALL, TagWriteStrategy.WRITE_ALL, True),
            (TagWriteStrategy.WRITE_ALL, TagWriteStrategy.WRITE_DEFAULT, False),
            (TagWriteStrategy.WRITE_ALL, "write_all", True),
            (TagWriteStrategy.WRITE_ALL, "WRITE_ALL", True),
            (TagWriteStrategy.WRITE_ALL, 123, False),
        ],
    )
    def test_eq_various_types(self, enum_member, other, expected):
        assert (enum_member == other) is expected

    def test_display_with_and_without_description(self):
        from manager.config_manager import ConfigEnum

        # TagWriteStrategy has description
        for member in TagWriteStrategy:
            disp = member.display
            assert member.name.replace("_", " ").title() in disp
            assert member.description in disp

        # Minimal enum without description property
        class NoDescriptionEnum(ConfigEnum):
            FOO = "foo"
            BAR = "bar"

        for member in NoDescriptionEnum:
            disp = member.display
            assert ":" not in disp
            assert member.name.replace("_", " ").title() == disp

    def test_find_none_returns_none(self):
        assert TagWriteStrategy.find(None) is None

    def test_find_not_found_returns_none(self):
        assert TagWriteStrategy.find("notfound") is None


class TestTagWriteStrategy:
    def test_enum_members_exist_and_are_unique(self):
        # Ensure all expected members exist and are unique
        members = list(TagWriteStrategy)
        names = [m.name for m in members]
        values = [m.value for m in members]
        # Check for duplicates
        assert len(set(names)) == len(names)
        assert len(set(values)) == len(values)
        # Check expected members (update as needed)
        expected = {"WRITE_ALL", "WRITE_DEFAULT", "OVERWRITE_DEFAULT"}
        actual = set(names)
        assert expected.issubset(actual)

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            (TagWriteStrategy.WRITE_ALL, "write_all"),
            (TagWriteStrategy.WRITE_DEFAULT, "write_default"),
            (TagWriteStrategy.OVERWRITE_DEFAULT, "overwrite_default"),
        ],
    )
    def test_enum_member_values(self, enum_member, expected_value):
        assert str(enum_member) == expected_value
        assert repr(enum_member) == f"<TagWriteStrategy.{enum_member.name}: '{expected_value}'>"

    @pytest.mark.parametrize(
        "value,expected_member",
        [
            ("write_all", TagWriteStrategy.WRITE_ALL),
            ("write_default", TagWriteStrategy.WRITE_DEFAULT),
            ("overwrite_default", TagWriteStrategy.OVERWRITE_DEFAULT),
        ],
    )
    def test_enum_from_value(self, value, expected_member):
        assert TagWriteStrategy(value) == expected_member

    def test_enum_invalid_value_raises(self):
        with pytest.raises(ValueError):
            TagWriteStrategy("not_a_valid_strategy")

    @pytest.mark.parametrize(
        "enum_member,expected",
        [
            (TagWriteStrategy.WRITE_ALL, False),
            (TagWriteStrategy.WRITE_DEFAULT, True),
            (TagWriteStrategy.OVERWRITE_DEFAULT, True),
        ],
    )
    def test_requires_default_tag_returns_expected(self, enum_member, expected):
        assert enum_member.requires_default_tag() is expected

    @pytest.mark.parametrize(
        "enum_member,expected",
        [
            (TagWriteStrategy.WRITE_ALL, "Update ratings for all discovered media players."),
            (TagWriteStrategy.WRITE_DEFAULT, "Update ratings only for the default player; do not remove other ratings."),
            (TagWriteStrategy.OVERWRITE_DEFAULT, "Update ratings only for the default player and delete all other ratings."),
        ],
    )
    def test_tag_write_strategy_description_matches(self, enum_member, expected):
        assert enum_member.description == expected


class TestConflictResolutionStrategy:
    def test_enum_members_exist_and_are_unique(self):
        # Ensure all expected members exist and are unique
        members = list(ConflictResolutionStrategy)
        names = [m.name for m in members]
        values = [m.value for m in members]
        # Check for duplicates
        assert len(set(names)) == len(names)
        assert len(set(values)) == len(values)
        # Check expected members (update as needed)
        expected = {"HIGHEST", "LOWEST"}
        actual = set(names)
        assert expected.issubset(actual)

    @pytest.mark.parametrize(
        "enum_member,expected_value",
        [
            (ConflictResolutionStrategy.HIGHEST, "highest"),
            (ConflictResolutionStrategy.LOWEST, "lowest"),
        ],
    )
    def test_enum_member_values(self, enum_member, expected_value):
        # str(enum_member) should return the value
        assert str(enum_member) == expected_value
        # repr(enum_member) should return the qualified name and value
        assert repr(enum_member) == f"<ConflictResolutionStrategy.{enum_member.name}: '{expected_value}'>"

    @pytest.mark.parametrize(
        "value,expected_member",
        [
            ("highest", ConflictResolutionStrategy.HIGHEST),
            ("lowest", ConflictResolutionStrategy.LOWEST),
        ],
    )
    def test_enum_from_value(self, value, expected_member):
        assert ConflictResolutionStrategy(value) == expected_member

    def test_enum_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ConflictResolutionStrategy("not_a_valid_strategy")

    @pytest.mark.parametrize(
        "enum_member,expected",
        [
            (ConflictResolutionStrategy.PRIORITIZED_ORDER, "Prioritized order of media players."),
            (ConflictResolutionStrategy.HIGHEST, "Use the highest rating."),
            (ConflictResolutionStrategy.LOWEST, "Use the lowest rating."),
            (ConflictResolutionStrategy.AVERAGE, "Use the average of all ratings."),
            (ConflictResolutionStrategy.CHOICE, "Prompt user to manually enter a rating."),
        ],
    )
    def test_conflict_resolution_strategy_description_matches(self, enum_member, expected):
        assert enum_member.description == expected


class TestParseArgs:
    def test_parse_args_valid(self, config_manager):
        assert config_manager.source == PlayerType.PLEX
        assert config_manager.destination == PlayerType.FILESYSTEM
        assert config_manager.default_tag == "MEDIAMONKEY"

    def test_parse_args_invalid_enum_value_raises(self, monkeypatch):
        argv = [
            "sync_ratings.py",
            "--source",
            "plex",
            "--destination",
            "invalid",
        ]
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit):
            from manager.config_manager import ConfigManager

            ConfigManager()

    def test_parse_args_missing_required_raises(self, monkeypatch, ConfigManager):
        argv = [
            "sync_ratings.py",
            "--source",
            "plex",
        ]
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit):
            ConfigManager()


class TestInitialize:
    @pytest.mark.parametrize(
        "config_manager",
        [
            (
                {
                    "log": "debug",
                    "source": "plex",
                    "destination": "mediamonkey",
                    "sync": "playlists",
                    "cache-mode": "metadata",
                    "tag-write-strategy": "write_default",
                    "conflict-resolution-strategy": "highest",
                    "tag-priority-order": "plex",
                }
            )
        ],
        indirect=["config_manager"],
    )
    def test_initialize_with_valid_args(self, config_manager):
        assert config_manager.log == LogLevel.DEBUG
        assert config_manager.source == PlayerType.PLEX
        assert config_manager.destination == PlayerType.MEDIAMONKEY
        assert config_manager.sync == ["playlists"]
        assert config_manager.cache_mode == "metadata"
        assert config_manager.tag_write_strategy == TagWriteStrategy.WRITE_DEFAULT
        assert config_manager.conflict_resolution_strategy == ConflictResolutionStrategy.HIGHEST
        assert config_manager.tag_priority_order == [PlayerType.PLEX]

    def test_initialize_missing_default_tag_raises(self, monkeypatch):
        argv = [
            "sync_ratings.py",
            "--source",
            "plex",
            "--destination",
            "filesystem",
            "--sync",
            "tracks",
            "--tag-write-strategy",
            "write_default",
        ]
        monkeypatch.setattr("sys.argv", argv)
        from manager.config_manager import ConfigManager

        with pytest.raises(ValueError):
            ConfigManager()


class TestConfigManager:
    def test_to_dict_structure(self, config_manager):
        result = config_manager.to_dict()
        assert isinstance(result, dict)
        # Check for expected top-level keys
        for key in ["source", "destination", "sync", "log", "tag_write_strategy", "default_tag", "conflict_resolution_strategy", "dry"]:
            assert key in result


class TestSaveConfig:
    @pytest.mark.parametrize(
        "runtime_change,expected_result",
        [
            # Simple override: should return the change
            ({"server": "test_server"}, {"server": "test_server"}),
            # Enum override: should return the change
            ({"tag_write_strategy": TagWriteStrategy.WRITE_ALL}, {"tag_write_strategy": TagWriteStrategy.WRITE_ALL}),
            # Nullify: should return the change
            ({"tag_write_strategy": None}, {"tag_write_strategy": None}),
            # No argument: current_config is None, expect no changes
            (None, {}),
            # Key missing in current_config: expect no changes
            ({"source": "plex"}, {}),
            # Normal diff scenario: should return the change
            ("default", {"server": "test_server"}),
        ],
    )
    def test_get_runtime_config_changes(self, config_manager, runtime_change, expected_result):
        # Set up config_manager to_dict to return runtime_overrides
        # (simulate current runtime config)
        runtime_config = config_manager.to_dict()
        # If runtime_change is None, skip update
        if runtime_change is None:
            result = config_manager._get_runtime_config_changes(runtime_config)
            assert result == expected_result
            return
        # If runtime_change is a string (e.g., 'default'), simulate a diff scenario
        if isinstance(runtime_change, str):
            runtime_config = {"server": "test_server"}
        else:
            runtime_config = {**runtime_config, **runtime_change}
        result = config_manager._get_runtime_config_changes(runtime_config)
        assert result == expected_result

    def test_get_runtime_config_gets_current_config(self, config_manager):
        # Test early exit if no runtime changes, and assert to_dict is called
        with patch.object(config_manager, "to_dict", wraps=config_manager.to_dict) as to_dict_spy:
            result = config_manager._get_runtime_config_changes(None)
            assert result == {}
            assert to_dict_spy.called

    def test_find_section_for_key_not_found(self, config_manager):
        # Should return empty string if key not found in any group
        result = config_manager._find_section_for_key("nonexistent_key")
        assert result == ""

    def test_get_config_key_name_not_found(self, config_manager):
        # Should return key if not found in any action
        result = config_manager._get_config_key_name("nonexistent_key")
        assert result == "nonexistent_key"

    @pytest.mark.parametrize(
        "config_manager, pending_change, log_calls, update_called",
        [
            ({"dry": True}, {"server": "TestServer", "sync": ["tracks", "playlists"]}, 1, False),
            ({"dry": False}, {}, 1, False),
            ({"dry": False}, {"server": "TestServer"}, 0, True),
        ],
        indirect=["config_manager"],
        ids=["dry", "no_changes", "changes"],
    )
    def test_save_config_no_update(self, config_manager, pending_change, log_calls, update_called):
        config_manager.logger = MagicMock(debug=MagicMock())
        config_manager._update_config_file = MagicMock()

        for key, value in pending_change.items():
            setattr(config_manager, key, value)

        config_manager.save_config()
        assert config_manager.logger.debug.call_count == log_calls
        assert config_manager._update_config_file.called == update_called
        if update_called:
            assert config_manager._update_config_file.call_args[0][0] == pending_change

    def test_save_config_nullify_key(self, config_manager):
        config_manager._update_config_file = MagicMock()

        config_manager.tag_write_strategy = None

        config_manager.save_config()
        assert config_manager._update_config_file.call_args[0][0] == {"tag_write_strategy": None}

    @pytest.mark.parametrize(
        "mock_updater, section_name, key_value, expect_add_section, expect_update_file, expect_value_set",
        [
            ([{"name": None, "has_key": False}], None, None, True, True, True),
            ([{"name": "plex", "has_key": False}], "plex", None, False, True, True),
            ([{"name": "plex", "has_key": True, "current_value": "OtherValue"}], "plex", "OtherValue", False, True, True),
            ([{"name": "PLEX", "has_key": True, "current_value": "OtherValue"}], "PLEX", "OtherValue", False, True, True),
            ([{"name": "plex", "has_key": True, "current_value": "TestServer"}], "plex", "TestServer", False, False, False),
            ([{"name": "not_plex", "has_key": True, "current_value": "OtherValue"}], "not_plex", "OtherValue", False, True, True),
            ([{"name": "not_plex", "has_key": True, "current_value": "TestServer"}], "not_plex", "TestServer", False, False, False),
        ],
        ids=[
            "section_missing_key_missing",
            "section_exists_key_missing",
            "section_exists_key_exists_value_differs",
            "section_key_all_caps",
            "section_exists_key_exists_value_same",
            "wrong_section_key_exists_value_differs",
            "wrong_section_key_exists_value_same",
        ],
        indirect=["mock_updater"],
    )
    def test_update_config_all_cases(self, monkeypatch, config_manager, mock_updater, section_name, key_value, expect_add_section, expect_update_file, expect_value_set):
        """
        Exercise ConfigManager._update_config_file for every relevant permutation
        of section / option existence and value equality, including wrong section scenarios.
        """
        cfg_key = "server"
        new_value = "TestServer"
        changes = {cfg_key: new_value}

        monkeypatch.setattr("manager.config_manager.ConfigUpdater", lambda: mock_updater)

        config_manager._update_config_file(changes)

        mock_updater.read.assert_called_once()

        expected_section_name = section_name if section_name else "plex"
        if expect_add_section:
            mock_updater.add_section.assert_called_once_with(expected_section_name)
        else:
            mock_updater.add_section.assert_not_called()

        if expect_update_file:
            mock_updater.update_file.assert_called_once()
        else:
            mock_updater.update_file.assert_not_called()

        target_section = mock_updater.sections.get(expected_section_name)
        if expect_value_set and target_section is not None:
            target_section.__setitem__.assert_called_once_with(cfg_key, new_value)
        elif target_section is not None:
            target_section.__setitem__.assert_not_called()

        if section_name != "plex" and section_name is not None:
            assert "plex" not in mock_updater.sections or mock_updater.sections["plex"].__setitem__.call_count == 0
            mock_updater.add_section.assert_not_called()

    def test_update_config_file_invalid_key_no_section(self, monkeypatch, config_manager, mock_updater):
        """
        Should skip update if no section found for key (invalid key scenario).
        Ensures no section is added and no file is updated.
        """
        cfg_key = "this_key_is_invalid"
        changes = {cfg_key: "irrelevant_value"}
        # Setup mock_updater to have no sections
        mock_updater.sections = {}

        monkeypatch.setattr("manager.config_manager.ConfigUpdater", lambda: mock_updater)
        config_manager._update_config_file(changes)
        mock_updater.read.assert_called_once()
        mock_updater.add_section.assert_not_called()
        mock_updater.update_file.assert_not_called()

    @pytest.mark.parametrize(
        "mock_updater",
        [([{"name": "test_section", "has_key": True, "current_value": "write_default"}])],
        indirect=True,
    )
    def test_update_config_file_nullify_key(self, monkeypatch, config_manager, mock_updater):
        """
        Should update config file when nullifying an existing key (tag_write_strategy) that is present and not None.
        Ensures update_file is called and the key is set to None.
        """

        changes = {"tag_write_strategy": None}
        monkeypatch.setattr("manager.config_manager.ConfigUpdater", lambda: mock_updater)
        config_manager._update_config_file(changes)
        mock_updater.read.assert_called_once()
        mock_updater.update_file.assert_called_once()
        target_section = mock_updater.sections.get("test_section")
        assert target_section is not None
        target_section.__setitem__.assert_called_once_with("tag-write-strategy", "")


class TestEnumFieldParsing:
    def test_parse_enum_field_list_invalid_raises(self, config_manager):
        # Patch _ENUM_FIELDS to use TagWriteStrategy for test
        config_manager._ENUM_FIELDS["tag_write_strategy"] = TagWriteStrategy
        with pytest.raises(ValueError):
            config_manager._parse_enum_field("tag_write_strategy", ["write_all", "not_a_strategy"])

    def test_parse_enum_field_single_invalid_raises(self, config_manager):
        config_manager._ENUM_FIELDS["tag_write_strategy"] = TagWriteStrategy
        with pytest.raises(ValueError):
            config_manager._parse_enum_field("tag_write_strategy", "not_a_strategy")


class TestStringifyValue:
    @pytest.mark.parametrize(
        "new_value,existing_line,expected",
        [
            (PlayerType.PLEX, "", "plex"),
            ("teststring", "", "teststring"),
            (["a", "b"], None, "[a, b]"),
            ("yes", "true # comment", "yes  # comment"),
            (False, "no   # comment", "false   # comment"),
            ("abc", "abcdef # comment", "abc    # comment"),
            ("abc", "abc   # comment", "abc   # comment"),
            ("abc", "", "abc"),
            ("abc", "abcde", "abc"),
            (None, "abcde", ""),
            (None, "abcde # comment", "      # comment"),
        ],
    )
    def test_stringify_value_branches(self, new_value, existing_line, expected):
        from manager.config_manager import stringify_value

        result = stringify_value(new_value, existing_line)
        assert result == expected
