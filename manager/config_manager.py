import logging
from enum import StrEnum
from typing import List

import configargparse
from configupdater import ConfigUpdater


class ConfigEnum(StrEnum):
    """Base class for case-insensitive string enums."""

    def __eq__(self, other: "ConfigEnum") -> bool:
        if isinstance(other, str):
            return self.value.lower() == other.lower()
        return super().__eq__(other)

    def __hash__(self):
        return hash(self.value.lower())

    @property
    def display(self) -> str:
        """Return the display name of the enum value."""
        name = self.name.replace("_", " ").title()
        return f"{name:<20} : {self.description}" if hasattr(self, "description") else name

    @classmethod
    def find(cls, value: str) -> "ConfigEnum":
        """Find an enum by its value, case-insensitive."""
        if value is None:
            return None

        for item in cls:
            if item.value.lower() == value.lower() or item.name.lower() == value.lower():
                return item
        return None


class LogLevel(ConfigEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"
    TRACE = "trace"


class PlayerType(ConfigEnum):
    PLEX = "plex"
    MEDIAMONKEY = "mediamonkey"
    FILESYSTEM = "filesystem"


class SyncItem(ConfigEnum):
    TRACKS = "tracks"
    PLAYLISTS = "playlists"


class CacheMode(ConfigEnum):
    METADATA = "metadata"
    MATCHES = "matches"
    MATCHES_ONLY = "matches-only"
    DISABLED = "disabled"


class TagWriteStrategy(ConfigEnum):
    WRITE_ALL = "write_all"
    WRITE_DEFAULT = "write_default"
    OVERWRITE_DEFAULT = "overwrite_default"

    @property
    def description(self) -> str:
        return {
            self.WRITE_ALL: "Update ratings for all discovered media players.",
            self.WRITE_DEFAULT: "Update ratings only for the default player; do not remove other ratings.",
            self.OVERWRITE_DEFAULT: "Update ratings only for the default player and delete all other ratings.",
        }[self]

    def requires_default_tag(self) -> bool:
        return self in {
            TagWriteStrategy.WRITE_DEFAULT,
            TagWriteStrategy.OVERWRITE_DEFAULT,
        }


class ConflictResolutionStrategy(ConfigEnum):
    PRIORITIZED_ORDER = "prioritized_order"
    HIGHEST = "highest"
    LOWEST = "lowest"
    AVERAGE = "average"
    CHOICE = "choice"

    @property
    def description(self) -> str:
        return {
            self.PRIORITIZED_ORDER: "Prioritized order of media players.",
            self.HIGHEST: "Use the highest rating.",
            self.LOWEST: "Use the lowest rating.",
            self.AVERAGE: "Use the average of all ratings.",
            self.CHOICE: "Prompt user to manually enter a rating.",
        }[self]


class ConfigManager:
    CONFIG_FILE = "./config.ini"
    _ENUM_FIELDS = {
        "log": LogLevel,
        "source": PlayerType,
        "destination": PlayerType,
        "sync": SyncItem,
        "cache_mode": CacheMode,
        "tag_write_strategy": TagWriteStrategy,
        "conflict_resolution_strategy": ConflictResolutionStrategy,
    }

    def __init__(self) -> None:
        """Initialize the configuration manager with default settings."""
        self.logger = logging.getLogger("PlexSync.ConfigManager")
        self.parser = configargparse.ArgumentParser(default_config_files=[self.CONFIG_FILE], description="Synchronizes ID3 and Vorbis music ratings between media players")
        self.config = self.parse_args()
        self._initialize_attributes()

    def parse_args(self) -> configargparse.Namespace:
        # Main section arguments
        main_group = self.parser.add_argument_group("Main Configuration")
        main_group.add_argument("-c", action="store_true", dest="clear_cache", help="Clear existing cache files before starting")
        main_group.add_argument("-d", "--dry", action="store_true", help="Does not apply any changes")
        main_group.add_argument("--source", type=str, required=True, help=f"Source player ({', '.join(PlayerType)})")
        main_group.add_argument("--destination", type=str, required=True, help=f"Destination player ({', '.join(PlayerType)})")
        main_group.add_argument("--sync", nargs="*", required=True, default=["tracks"], help=f"Selects which items to sync: one or more of {list(SyncItem)}")
        main_group.add_argument("--log", type=str, default=LogLevel.WARNING, help=f"Sets the logging level ({', '.join(LogLevel)})")
        main_group.add_argument("--cache-mode", type=str, choices=list(CacheMode), default=CacheMode.METADATA, help="Cache mode: metadata, matches, matches-only, disabled")

        # Plex section arguments
        plex_group = self.parser.add_argument_group("Plex Configuration")
        plex_group.add_argument("--server", type=str, help="The name of the Plex media server")
        plex_group.add_argument("--username", type=str, help="The Plex username")
        plex_group.add_argument("--passwd", type=str, help="The password for the Plex user. NOT RECOMMENDED TO USE!")
        plex_group.add_argument("--token", type=str, help="Plex API token. See Plex documentation for details")

        # Filesystem section arguments
        filesystem_group = self.parser.add_argument_group("Filesystem Configuration")
        filesystem_group.add_argument("--path", type=str, help="Path to music directory for filesystem player")
        filesystem_group.add_argument("--playlist-path", type=str, help="Path to playlists directory for filesystem player")
        filesystem_group.add_argument("--tag-write-strategy", type=str, choices=[strategy.value for strategy in TagWriteStrategy], help="Strategy for writing rating tags to files")
        filesystem_group.add_argument("--default-tag", type=str, help="Canonical tag to use for writing ratings (e.g., MEDIAMONKEY, WINDOWSMEDIAPLAYER, MUSICBEE, WINAMP, TEXT)")
        filesystem_group.add_argument(
            "--conflict-resolution-strategy",
            type=str,
            choices=[strategy.value for strategy in ConflictResolutionStrategy],
            help="Strategy for resolving conflicting rating values",
        )
        filesystem_group.add_argument("--tag-priority-order", type=str, nargs="+", help="Ordered list of tag identifiers for resolving conflicts")

        return self.parser.parse_args()

    def _initialize_attributes(self) -> None:
        """Set attributes from parsed config, including enum coercion and validation."""
        for key, value in vars(self.config).items():
            if key in self._ENUM_FIELDS:
                setattr(self, key, self._parse_enum_field(key, value))
            else:
                setattr(self, key, value)

        self._validate_config_requirements()
        self.logger.debug(f"Current runtime configuration: {self.to_dict()}")

    def _parse_enum_field(self, key: str, value: ConfigEnum) -> ConfigEnum | list[ConfigEnum] | None:
        """Convert CLI string(s) to corresponding ConfigEnum value(s), with error checking."""
        enum_class = self._ENUM_FIELDS[key]
        if value is None:
            return None

        if isinstance(value, list):
            parsed = []
            for v in value:
                enum_value = enum_class.find(v)
                if enum_value is None:
                    raise ValueError(f"Invalid value '{v}' for '{key}'. Valid options: {', '.join(e.value for e in enum_class)}")
                parsed.append(enum_value)
            return parsed
        else:
            enum_value = enum_class.find(value)
            if enum_value is None:
                raise ValueError(f"Invalid value '{value}' for '{key}'. Valid options: {', '.join(e.value for e in enum_class)}")
            return enum_value

    def _validate_config_requirements(self) -> None:
        """Perform validations that require multiple config values."""
        strategy = getattr(self, "tag_write_strategy", None)
        requires_default = strategy in {
            TagWriteStrategy.WRITE_DEFAULT,
            TagWriteStrategy.OVERWRITE_DEFAULT,
        }

        if requires_default and not getattr(self, "default_tag", None):
            raise ValueError(f"default_tag must be set when using the '{strategy}' strategy.")

    def to_dict(self) -> dict:
        """Returns a dictionary of current runtime values stored in the ConfigManager instance.The keys correspond to the 'dest' values of the parser actions."""
        config_dict = {}
        for action in self.parser._actions:
            key = action.dest
            if key and hasattr(self, key):
                config_dict[key] = getattr(self, key)
        return config_dict

    def save_config(self) -> None:
        current_config = self.to_dict()
        changes = self._get_runtime_config_changes(current_config)

        if not changes:
            self.logger.debug("No config changes detected.")
            return

        if self.dry:
            self.logger.debug("Dry-run: config changes detected but not saved.")
            return

        self._update_config_file(changes)

    def _get_runtime_config_changes(self, current_config: dict | None = None) -> dict:
        if current_config is None:
            current_config = self.to_dict()

        loaded_config = self.parser.parse_args()
        changes = {}

        for key, file_value in vars(loaded_config).items():
            if key not in current_config:
                continue

            runtime_value = current_config[key]

            if isinstance(runtime_value, list):
                if set(runtime_value or []) != set(file_value or []):
                    changes[key] = runtime_value
            else:
                if runtime_value != file_value:
                    changes[key] = runtime_value

        return changes

    def _find_section_for_key(self, key: str) -> str:
        for group in self.parser._action_groups:
            if any(a.dest == key for a in group._group_actions):
                return group.title.lower().replace(" configuration", "").strip()
        return ""

    def _get_config_key_name(self, key: str) -> str:
        for action in self.parser._option_string_actions.values():
            if action.dest == key:
                return next((opt.lstrip("-") for opt in action.option_strings if opt.startswith("--")), key)
        return key

    def _update_config_file(self, changes: dict) -> None:
        updater = ConfigUpdater()
        updater.read(self.CONFIG_FILE)
        changed = False

        for key, value in changes.items():
            expected_section = self._find_section_for_key(key)
            cfg_key = self._get_config_key_name(key)

            current_section = next(
                (s.name for s in updater.iter_sections() if s.has_option(cfg_key)),
                None,
            )
            target = current_section or expected_section
            if not target:
                continue

            if not updater.has_section(target):
                updater.add_section(target)

            opt = updater[target].get(cfg_key)
            old_line = opt.value if opt else ""
            new_val = stringify_value(value, old_line)

            # Only set if value is different
            if opt is None or old_line != new_val:
                updater[target][cfg_key] = new_val
                changed = True

        if changed:
            updater.update_file(self.CONFIG_FILE)


def stringify_value(new_value: str | bool | None | List[str], existing_line: str | None = "") -> str:
    """
    Converts the new value into a config file string while preserving inline comments and padding.
    - `existing_line` should be the original raw value string (e.g., 'true     # keep this')
    """
    existing_line = existing_line or ""
    # Extract comment
    lhs, sep, comment = existing_line.partition("#")
    lhs_stripped = lhs.rstrip()
    padding = lhs[len(lhs_stripped) :] if lhs else ""

    # Build new value string
    if new_value is None:
        value_str = ""
    elif isinstance(new_value, list):
        value_str = "[" + ", ".join(new_value) + "]"
    elif isinstance(new_value, bool):
        value_str = "true" if new_value else "false"
    else:
        value_str = str(new_value)

    # Re-apply original padding if new value is shorter
    if len(value_str) < len(lhs_stripped):
        value_str = value_str + " " * (len(lhs_stripped) - len(value_str))

    # Include original spacing + inline comment
    return f"{value_str}{padding}#{comment}" if sep else value_str.rstrip()
