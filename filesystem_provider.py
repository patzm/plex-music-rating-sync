import abc
import logging
from enum import StrEnum
from os.path import relpath
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mutagen
from mutagen import FileType
from mutagen.flac import FLAC
from mutagen.id3 import ID3, POPM, TALB, TIT2, TPE1, TRCK, TXXX, ID3FileType
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from manager import get_manager
from manager.config_manager import ConflictResolutionStrategy, TagWriteStrategy
from ratings import Rating, RatingScale
from sync_items import AudioTag, Playlist
from ui.help import ShowHelp
from ui.prompt import UserPrompt


class DefaultPlayerTags(StrEnum):
    WINDOWSMEDIAPLAYER = "POPM:Windows Media Player 9 Series"
    MEDIAMONKEY = "POPM:no@email"
    MUSICBEE = "POPM:MusicBee"
    WINAMP = "POPM:rating@winamp.com"
    TEXT = "TXXX:RATING"


class ID3Field(StrEnum):
    ARTIST = "TPE1"
    ALBUM = "TALB"
    TITLE = "TIT2"
    TRACKNUMBER = "TRCK"


class VorbisField(StrEnum):
    FMPS_RATING = "FMPS_RATING"
    RATING = "RATING"
    ARTIST = "ARTIST"
    ALBUM = "ALBUM"
    TITLE = "TITLE"
    TRACKNUMBER = "TRACKNUMBER"


class ID3TagRegistry:
    def __init__(self):
        self._entries_by_key = {}

        initial_entries = {
            "WINDOWSMEDIAPLAYER": {"id3_tag": DefaultPlayerTags.WINDOWSMEDIAPLAYER, "player_name": "Windows Media Player"},
            "MEDIAMONKEY": {"id3_tag": DefaultPlayerTags.MEDIAMONKEY, "player_name": "MediaMonkey"},
            "MUSICBEE": {"id3_tag": DefaultPlayerTags.MUSICBEE, "player_name": "MusicBee"},
            "WINAMP": {"id3_tag": DefaultPlayerTags.WINAMP, "player_name": "Winamp"},
            "TEXT": {"id3_tag": DefaultPlayerTags.TEXT, "player_name": "Text"},
        }

        for tag_key, entry in initial_entries.items():
            self.register(id3_tag=entry["id3_tag"], tag_key=tag_key, player_name=entry["player_name"])

    def register(self, id3_tag: str, tag_key: str | None = None, player_name: str | None = None) -> str:
        """Register a tag by ID3 tag string, returning its tag_key (creating one if necessary)."""
        tag_key = tag_key.upper() if tag_key else f"UNKNOWN{len(self._entries_by_key)}"

        # If already registered by ID3 tag, return existing key
        existing_key = self.get_key_for_id3_tag(id3_tag)
        if existing_key:
            return existing_key

        self._entries_by_key[tag_key] = {
            "id3_tag": id3_tag,
            "player_name": player_name or id3_tag,
        }

        return tag_key

    def get_id3_tag_for_key(self, tag_key: str) -> str | None:
        if not tag_key:
            return None
        return self._entries_by_key.get(tag_key.upper(), {}).get("id3_tag")

    def get_player_name_for_key(self, tag_key: str) -> str | None:
        if not tag_key:
            return None
        return self._entries_by_key.get(tag_key.upper(), {}).get("player_name")

    def get_key_for_id3_tag(self, id3_tag: str) -> str | None:
        if not id3_tag:
            return None
        id3_tag_upper = id3_tag.upper()
        for key, entry in self._entries_by_key.items():
            if entry["id3_tag"].upper() == id3_tag_upper:
                return key
        return None

    def get_key_for_player_name(self, player_name: str) -> str | None:
        if not player_name:
            return None
        player_lower = player_name.lower()
        for key, entry in self._entries_by_key.items():
            if entry["player_name"].lower() == player_lower:
                return key
        return None

    def resolve_key_from_input(self, input_str: str) -> str | None:
        upper = input_str.upper()
        if upper in self._entries_by_key:
            return upper
        return self.get_key_for_id3_tag(input_str) or self.get_key_for_player_name(input_str)

    def known_keys(self) -> List[str]:
        return list(self._entries_by_key.keys())

    def get_config_value(self, tag_key: str) -> str:
        """Return config-friendly value for this tag_key. If UNKNOWN, return the raw ID3 tag."""
        entry = self._entries_by_key.get(tag_key)
        if not entry:
            return tag_key
        if tag_key.startswith("UNKNOWN"):
            return entry["id3_tag"]
        return tag_key

    def display_name(self, value: str) -> str:
        tag_key = self.resolve_key_from_input(value)
        return self.get_player_name_for_key(tag_key) or value

    def get_popm_email_for_key(self, key: str) -> str | None:
        tag = self.get_id3_tag_for_key(key)
        if tag and tag.upper().startswith("POPM:"):
            return tag.split(":", 1)[1]
        return None


class AudioTagHandler(abc.ABC):
    def __init__(self, tagging_policy: dict | None = None, **kwargs):
        self.logger = logging.getLogger(f"PlexSync.{self.__class__.__name__}")
        self.stats_mgr = get_manager().get_stats_manager()

        self.conflict_resolution_strategy = tagging_policy.get("conflict_resolution_strategy") if tagging_policy else None
        self.tag_write_strategy = tagging_policy.get("tag_write_strategy") if tagging_policy else None
        self.default_tag = tagging_policy.get("default_tag") if tagging_policy else None
        self.tag_priority_order = tagging_policy.get("tag_priority_order") if tagging_policy else None
        self.prompt = UserPrompt()

    # ------------------------------
    # Phase 1: Metadata Extraction
    # ------------------------------
    @abc.abstractmethod
    def can_handle(self, file: FileType) -> bool:
        """Return True if this handler supports the given file."""
        raise NotImplementedError("can_handle() not implemented")

    @abc.abstractmethod
    def extract_metadata(self, audio_file: FileType) -> Tuple[AudioTag, Dict[str, Any]]:
        """Read *only* raw rating-tag values from the file."""
        raise NotImplementedError("extract_metadata() not implemented")

    # ------------------------------
    # Phase 2: Normalization
    # ------------------------------
    @abc.abstractmethod
    def _try_normalize(self, raw_value: str | float, tag_key: str) -> Rating | None:
        """
        Attempt to convert a single raw tag_value into a Rating.
        Subclasses implement scale inference or parsing here.
        """
        raise NotImplementedError("Subclasses must implement _try_normalize()")

    def resolve_rating(self, raw_ratings: Dict[str, str | float], track: AudioTag) -> Rating | None:
        """Attempt to resolve a final rating based on raw values and context."""
        normalized: Dict[str, Rating] = {}
        failed: set[str] = set()

        for tag_key, raw_value in raw_ratings.items():
            rating = self._try_normalize(raw_value, tag_key)
            if rating is not None:
                normalized[tag_key] = rating
            else:
                failed.add(tag_key)

        if not normalized:
            # all failed = unrated
            return Rating.unrated()

        if failed:
            # some succeeded, some failed = ambiguous
            self.logger.debug(f"Rating ambiguity in {track.ID}: could not normalize {failed}, deferring resolution")
            return None

        unique_ratings = set(normalized.values())
        if len(unique_ratings) == 1:
            return next(iter(unique_ratings))

        # genuine conflict
        return self._resolve_conflict(normalized, track)

    def _resolve_conflict(self, ratings_by_tag: Dict[str, Rating], track: AudioTag) -> Rating | None:
        strat = self.conflict_resolution_strategy
        if not strat:
            return None

        if not self.is_strategy_supported(strat):
            self.logger.warning(f"Unsupported strategy {strat}; defaulting to HIGHEST")
            strat = ConflictResolutionStrategy.HIGHEST

        strategy_handler = {
            ConflictResolutionStrategy.CHOICE: self._resolve_choice,
            ConflictResolutionStrategy.PRIORITIZED_ORDER: self._resolve_prioritized_order,
            ConflictResolutionStrategy.HIGHEST: self._resolve_highest,
            ConflictResolutionStrategy.LOWEST: self._resolve_lowest,
            ConflictResolutionStrategy.AVERAGE: self._resolve_average,
        }.get(strat)

        return strategy_handler(ratings_by_tag, track)

    def _resolve_prioritized_order(self, ratings_by_tag: Dict[str, Rating], track: AudioTag) -> Rating | None:
        if not self.tag_priority_order:
            self.logger.warning("No tag_priority_order for PRIORITIZED_ORDER")
            raise ValueError("No tag_priority_order for PRIORITIZED_ORDER")
        for key in self.tag_priority_order:
            if key in ratings_by_tag:
                return ratings_by_tag[key]
        return Rating.unrated()

    def _resolve_highest(self, ratings_by_tag: Dict[str, Rating], track: AudioTag) -> Rating | None:
        return max(ratings_by_tag.values())

    def _resolve_lowest(self, ratings_by_tag: Dict[str, Rating], track: AudioTag) -> Rating | None:
        return min(ratings_by_tag.values())

    def _resolve_average(self, ratings_by_tag: Dict[str, Rating], track: AudioTag) -> Rating | None:
        vals = [r.to_float(RatingScale.NORMALIZED) for r in ratings_by_tag.values()]
        return Rating(sum(vals) / len(vals)) if vals else None

    def _resolve_choice(self, ratings_by_tag: Dict[str, Rating], track: AudioTag) -> Rating | None:
        """Default interactive chooser: list each tag and rating, let user pick or skip."""
        items = list(ratings_by_tag.items())

        menu_options = [UserPrompt.MenuOption(key=key, label=f"{self.tag_registry.get_player_name_for_key(key):<30} : {rating.to_display()}") for key, rating in items]
        menu_options.append(UserPrompt.MenuOption(key=None, label="Skip (no rating)"))
        selected_key = self.prompt.choice(
            f"Conflicting ratings for {track.artist} - {track.album} - {track.title}:", menu_options, help_text="Select the rating to use for this track, or skip to leave unrated."
        )
        if selected_key is None:
            return None
        return ratings_by_tag[selected_key]

    def is_strategy_supported(self, strategy: ConflictResolutionStrategy) -> bool:
        """Override if a strategy isn't valid for this format."""
        return strategy in list(ConflictResolutionStrategy)

    # ------------------------------
    # Phase 3: Finalization / Scale Inference
    # ------------------------------
    @abc.abstractmethod
    def finalize_rating_strategy(self, conflicts: List[dict]) -> None:
        """
        After a full scan, infer dominant scales or prompt for strategy settings.
        """
        raise NotImplementedError("finalize_rating_strategy() not implemented")

    # ------------------------------
    # Phase 4: Writing
    # ------------------------------
    @abc.abstractmethod
    def apply_tags(self, audio_file: FileType, audio_tag: Optional[Dict[str, Any]], rating: Rating | None = None) -> FileType:
        """Write metadata fields and the resolved rating back to the file."""
        raise NotImplementedError("apply_tags() not implemented")

    # ------------------------------
    # Phase 5: Orchestration
    # ------------------------------
    def read_tags(self, audio_file: FileType) -> Tuple[AudioTag, Dict[str, str | int | float] | None]:
        """
        Full tag-reading workflow:
          1. extract_metadata()
          2. resolve_rating()
          3. return (AudioTag, None) if we got a rating,
             or (AudioTag, raw_ratings) to defer if unresolved.
        """
        track, raw = self.extract_metadata(audio_file)
        rating = self.resolve_rating(raw, track)
        if rating is not None and not rating.is_unrated:
            track.rating = rating
            return track, None
        return track, raw

    # ------------------------------
    # Utility
    # ------------------------------
    def _show_conflicts(self, conflicts: List[dict]) -> None:  # pragma: no cover
        print("\nConflicts:")
        for conflict in conflicts:
            if conflict.get("handler") is not self:
                continue
            track = conflict["track"]
            raw = conflict.get("raw", {})
            print(f"\n  {track.artist} — {track.album} — {track.title}")
            for tag_key, raw_val in raw.items():
                rating = self._try_normalize(raw_val, tag_key)
                label = self._get_label(tag_key)
                print(f"    {label:<30} : {rating.to_display() if rating else str(raw_val)}")

    def _get_label(self, tag_key: str) -> str:  # pragma: no cover
        """Override in subclass to map tag keys to user-friendly labels."""
        return tag_key


class VorbisHandler(AudioTagHandler):
    def __init__(self, tagging_policy: dict | None = None, **kwargs):
        super().__init__(tagging_policy=tagging_policy, **kwargs)
        self.fmps_rating_scale: RatingScale | None = None
        self.rating_scale: RatingScale | None = None
        self.aggressive_inference = False
        self.conflict_resolution_strategy = ConflictResolutionStrategy.HIGHEST

    # ------------------------------
    # Phase 1: Metadata Extraction
    # ------------------------------
    def extract_metadata(self, audio_file: FileType) -> Tuple[AudioTag, Dict[str, Any]]:
        tag = self._get_audiotag(audio_file, audio_file.filename)
        raw: Dict[str, Any] = {}
        for field in (VorbisField.FMPS_RATING, VorbisField.RATING):
            key = field.value
            if key in audio_file.tags:
                raw[key] = audio_file.get(key, [None])[0]
        return tag, raw

    def _get_audiotag(self, audio_file: FileType, file_path: str) -> AudioTag:
        track_num = audio_file.get(VorbisField.TRACKNUMBER, ["0"])[0]
        duration = getattr(getattr(audio_file, "info", None), "length", -1)
        return AudioTag(
            artist=audio_file.get(VorbisField.ARTIST, [""])[0],
            album=audio_file.get(VorbisField.ALBUM, [""])[0],
            title=audio_file.get(VorbisField.TITLE, [""])[0],
            file_path=file_path,
            ID=file_path.lower(),
            track=int(track_num.split("/")[0]),
            duration=int(duration or -1),
            rating=None,
        )

    # ------------------------------
    # Phase 2: Normalization Hook
    # ------------------------------
    def _try_normalize(self, raw_value: str | float, tag_key: str) -> Rating | None:
        scale = self.fmps_rating_scale if tag_key == VorbisField.FMPS_RATING.value else self.rating_scale
        rating = Rating.try_create(raw_value, scale=scale, aggressive=self.aggressive_inference)

        if not scale and rating:
            self.stats_mgr.increment(f"VorbisHandler::inferred_scale::{tag_key}::{rating.scale.name}")

        if rating is not None:
            return rating

        if self.aggressive_inference:
            return Rating.unrated()

        return None

    def is_strategy_supported(self, strategy: ConflictResolutionStrategy) -> bool:
        return strategy != ConflictResolutionStrategy.PRIORITIZED_ORDER

    # ------------------------------
    # Phase 3: Finalization / Scale Inference
    # ------------------------------
    def finalize_rating_strategy(self, conflicts: List[dict]) -> None:
        conflicts = [c for c in conflicts if c.get("handler") is self]
        # self._print_summary()

        # NOTE: Vorbis tags have two different rating scales in common use:
        def pick_scale(field: VorbisField) -> RatingScale | None:
            stats = self.stats_mgr.get(f"VorbisHandler::inferred_scale::{field.value}")
            if not stats:
                return None
            max_count = max(stats.values())
            tied = [RatingScale[k] for k, v in stats.items() if v == max_count]
            return sorted(tied, key=lambda s: s != RatingScale.NORMALIZED)[0]

        self.fmps_rating_scale = pick_scale(VorbisField.FMPS_RATING)
        self.rating_scale = pick_scale(VorbisField.RATING)
        self.aggressive_inference = True

    def _print_summary(self) -> None:  # pragma: no cover
        """Print the stats of inferred scales and current strategy."""
        print("\nFLAC/OGG (Vorbis) Ratings Summary:")
        for field in (VorbisField.RATING, VorbisField.FMPS_RATING):
            stats = self.stats_mgr.get(f"VorbisHandler::inferred_scale::{field.value}")
            if stats:
                print(f"  {field.value} scale usage:")
                for scale, count in stats.items():
                    print(f"    - {scale}: {count}")
        if self.conflict_resolution_strategy:
            print(f"\n  Conflict Strategy: {self.conflict_resolution_strategy.display}\n")

    # ------------------------------
    # Phase 4: Writing
    # ------------------------------
    def apply_tags(self, audio_file: FileType, audio_tag: AudioTag | None = None, rating: Rating | None = None) -> FileType:
        """Write metadata fields and resolved rating back into the Vorbis tags."""
        if audio_tag:
            if audio_tag.title:
                audio_file["TITLE"] = [audio_tag.title]
            if audio_tag.artist:
                audio_file["ARTIST"] = [audio_tag.artist]
            if audio_tag.album:
                audio_file["ALBUM"] = [audio_tag.album]
            if audio_tag.track is not None:
                audio_file["TRACKNUMBER"] = [str(audio_tag.track)]

        if rating is not None:
            if self.fmps_rating_scale is not None:
                audio_file[VorbisField.FMPS_RATING] = rating.to_str(self.fmps_rating_scale)
            if self.rating_scale is not None:
                audio_file[VorbisField.RATING] = rating.to_str(self.rating_scale)
        return audio_file

    # ------------------------------
    # Utility
    # ------------------------------
    def can_handle(self, file: FileType) -> bool:
        return isinstance(file, (OggVorbis, OggOpus, FLAC))


class ID3Handler(AudioTagHandler):
    def __init__(self, tagging_policy: dict | None = None, **kwargs):
        super().__init__(tagging_policy=tagging_policy, **kwargs)
        self.tag_registry = ID3TagRegistry()
        self.discovered_rating_tags: set[str] = set()
        self.prompt = UserPrompt()

    # ------------------------------
    # Phase 1: Metadata Extraction
    # ------------------------------
    def extract_metadata(self, audio_file: FileType) -> Tuple[AudioTag, Dict[str, Any]]:
        # Build the AudioTag skeleton
        track = self._get_audiotag(
            audio_file.tags,
            audio_file.filename,
            duration=int(getattr(audio_file.info, "length", -1)),
        )

        raw: Dict[str, Any] = {}
        for frame_key, frame in audio_file.tags.items():
            # Only pull rating frames
            if not (frame_key.startswith("POPM:") or frame_key == "TXXX:RATING"):
                continue

            tag_key = self.tag_registry.register(frame_key)
            self.discovered_rating_tags.add(tag_key)
            self.stats_mgr.increment(f"ID3Handler::tags_used::{tag_key}")

            if isinstance(frame, POPM):
                raw[tag_key] = str(frame.rating)
            elif isinstance(frame, TXXX):
                raw[tag_key] = frame.text[0] if frame.text else None

        return track, raw

    def _get_audiotag(self, audio_file: FileType, file_path: str, duration: int = -1) -> AudioTag:
        def _safe(field: str) -> str:
            return audio_file.get(field).text[0] if audio_file.get(field) else ""

        track = _safe("TRCK") or "0"
        return AudioTag(
            artist=_safe("TPE1"),
            album=_safe("TALB"),
            title=_safe("TIT2"),
            file_path=file_path,
            rating=None,
            ID=file_path.lower(),
            track=int(track.split("/")[0]) if "/" in track else int(track),
            duration=duration,
        )

    # ------------------------------
    # Phase 2: Normalization
    # ------------------------------
    def _try_normalize(self, raw_value: str | float, tag_key: str) -> Rating | None:
        id3_tag = self.tag_registry.get_id3_tag_for_key(tag_key) or ""
        if id3_tag.upper().startswith("POPM:") or tag_key.upper().startswith("POPM:"):
            scale = RatingScale.POPM
        elif not id3_tag:
            raise ValueError(f"Unknown tag_key '{tag_key}'.")
        else:
            scale = RatingScale.ZERO_TO_FIVE
        return Rating.try_create(raw_value, scale=scale) or Rating.unrated()

    # ------------------------------
    # Phase 3: Finalization / Scale Inference
    # ------------------------------

    def finalize_rating_strategy(self, conflicts: list[dict]) -> None:
        conflicts = [c for c in conflicts if c.get("handler") is self]

        tag_counts = self.stats_mgr.get("ID3Handler::tags_used") or {}
        has_multiple = len(tag_counts) > 1
        has_conflicts = len(conflicts) > 0

        def _settings_required() -> bool:
            return any(
                [
                    self.conflict_resolution_strategy is None and has_conflicts,
                    self.conflict_resolution_strategy == ConflictResolutionStrategy.PRIORITIZED_ORDER and has_multiple and not self.tag_priority_order,
                    has_multiple and not self.tag_write_strategy,
                    self.tag_write_strategy and self.tag_write_strategy.requires_default_tag() and not self.default_tag,
                ]
            )

        if not _settings_required():
            return  # No settings required, exit early

        self._print_summary()

        # Conflict resolution strategy prompt
        if self.conflict_resolution_strategy is None and has_conflicts:
            menu_options = [UserPrompt.MenuOption(key=strat, label=strat.display) for strat in ConflictResolutionStrategy]
            menu_options.append(UserPrompt.MenuOption(key="show_conflicts", label="Show conflicts"))
            selected_key = self.prompt.choice(
                "\nHow should rating conflicts be resolved when different media players have stored different ratings for the same track?\n \
                    Choose a strategy below. This affects how those ratings are interpreted:",
                menu_options,
                help_text=ShowHelp.ConflictResolution,
            )
            if selected_key == "show_conflicts":
                self._show_conflicts(conflicts)
                # re-prompt
                return self.finalize_rating_strategy(conflicts)
            else:
                self.conflict_resolution_strategy = selected_key

        # Tag priority order prompt
        if self.conflict_resolution_strategy == ConflictResolutionStrategy.PRIORITIZED_ORDER and has_multiple and not self.tag_priority_order:
            player_name_to_key = {self.tag_registry.get_player_name_for_key(k): k for k in tag_counts}
            menu_options = [UserPrompt.MenuOption(key=key, label=player_name) for player_name, key in player_name_to_key.items()]
            order_keys = self.prompt.choice(
                "\nMultiple media players have written ratings to this file. Please choose the order of preference (highest first).\n\
                    This determines which player's rating takes priority when they conflict.",
                menu_options,
                allow_multiple=True,
                help_text=ShowHelp.TagPriority,
            )
            self.tag_priority_order = order_keys

        # Write strategy prompt
        if has_multiple and not self.tag_write_strategy:
            menu_options = [UserPrompt.MenuOption(key=strat, label=strat.display) for strat in TagWriteStrategy]
            selected_key = self.prompt.choice(
                "\nHow should ratings be written back to your files?\nChoose a write strategy. This controls which tags are updated:",
                menu_options,
                help_text=ShowHelp.WriteStrategy,
            )
            self.tag_write_strategy = selected_key

        # Preferred player tag prompt
        if self.tag_write_strategy and self.tag_write_strategy.requires_default_tag() and not self.default_tag:
            player_name_to_key = {self.tag_registry.get_player_name_for_key(k): k for k in tag_counts}
            menu_options = [UserPrompt.MenuOption(key=key, label=player_name) for player_name, key in player_name_to_key.items()]
            selected_key = self.prompt.choice(
                "Which media player do you use most often to view or manage ratings?\nSelect the one whose format should be used to store ratings in your files:",
                menu_options,
                help_text=ShowHelp.PreferredPlayerTag,
            )
            self.default_tag = selected_key

        # if this point is reached, need to prompt to save settings
        cfg = get_manager().get_config_manager()
        cfg.conflict_resolution_strategy = self.conflict_resolution_strategy
        cfg.tag_write_strategy = self.tag_write_strategy
        cfg.default_tag = self.default_tag
        cfg.tag_priority_order = self.tag_priority_order

        if self.prompt.yes_no("\nSave settings to config.ini?"):
            cfg.save_config()

    def _print_summary(self) -> None:  # pragma: no cover
        print("\nMP3 Ratings Summary:")
        used = self.stats_mgr.get("ID3Handler::tags_used") or {}
        for key, cnt in used.items():
            player = self.tag_registry.get_player_name_for_key(key) or key
            print(f"  {player}: {cnt}")
        if self.conflict_resolution_strategy:
            print(f"\nConflict strategy: {self.conflict_resolution_strategy.display}")
        if self.tag_write_strategy:
            print(f"Write strategy: {self.tag_write_strategy.display}")
        if self.default_tag:
            dt = self.tag_registry.get_player_name_for_key(self.default_tag) or self.default_tag
            print(f"Default tag: {dt}")

    # ------------------------------
    # Phase 4: Writing
    # ------------------------------
    def _apply_metadata_fields(self, audio_file: FileType, audio_tag: AudioTag) -> None:
        if audio_tag.title:
            audio_file.tags[ID3Field.TITLE] = TIT2(encoding=3, text=audio_tag.title)
        if audio_tag.artist:
            audio_file.tags[ID3Field.ARTIST] = TPE1(encoding=3, text=audio_tag.artist)
        if audio_tag.album:
            audio_file.tags[ID3Field.ALBUM] = TALB(encoding=3, text=audio_tag.album)
        if audio_tag.track:
            audio_file.tags[ID3Field.TRACKNUMBER] = TRCK(encoding=3, text=str(audio_tag.track))

    def _determine_tags_to_write(self) -> set[str]:
        if self.tag_write_strategy == TagWriteStrategy.OVERWRITE_DEFAULT:
            return {self.default_tag}
        elif self.tag_write_strategy == TagWriteStrategy.WRITE_ALL:
            return self.discovered_rating_tags
        elif self.tag_write_strategy == TagWriteStrategy.WRITE_DEFAULT:
            return {self.default_tag}
        else:
            return set()

    def apply_tags(self, audio_file: FileType, audio_tag: AudioTag | None = None, rating: Rating | None = None) -> FileType:
        if audio_file.tags is None or not isinstance(audio_file.tags, ID3):
            audio_file.tags = ID3()

        if audio_tag:
            self._apply_metadata_fields(audio_file, audio_tag)

        if rating is not None:
            if self.tag_write_strategy == TagWriteStrategy.OVERWRITE_DEFAULT:
                self._remove_existing_id3_tags(audio_file)

            to_write = self._determine_tags_to_write()
            if to_write:
                audio_file = self._apply_rating(audio_file, rating, to_write)

        return audio_file

    def _apply_rating(self, audio_file: FileType, rating: Rating, tag_keys: set[str]) -> FileType:
        for key in tag_keys:
            id3_tag = self.tag_registry.get_id3_tag_for_key(key)
            if not id3_tag:
                self.logger.warning(f"No tag for key '{key}'")
                continue

            if id3_tag == "TXXX:RATING":
                txt = rating.to_str(RatingScale.ZERO_TO_FIVE)
                if "TXXX:RATING" in audio_file.tags:
                    audio_file.tags["TXXX:RATING"].text = [txt]
                else:
                    audio_file.tags.add(TXXX(encoding=1, desc="RATING", text=[txt]))

            if id3_tag.upper().startswith("POPM:"):
                pr = rating.to_int(RatingScale.POPM)
                email = self.tag_registry.get_popm_email_for_key(key) or ""
                if id3_tag in audio_file.tags:
                    audio_file.tags[id3_tag].rating = pr
                else:
                    audio_file.tags.add(POPM(email=email, rating=pr, count=0))

        return audio_file

    def _remove_existing_id3_tags(self, audio_file: FileType) -> None:
        for frame in list(audio_file.keys()):
            if frame == "TXXX:RATING" or frame.startswith("POPM:"):
                del audio_file[frame]

    # ------------------------------
    # Utility
    # ------------------------------
    def can_handle(self, file: FileType) -> bool:
        return isinstance(file, ID3FileType)

    def _get_label(self, tag_key: str) -> str:  # pragma: no cover
        return self.tag_registry.display_name(tag_key)


class FileSystemProvider:
    AUDIO_EXT = {".mp3", ".flac", ".ogg", ".m4a", ".wav", ".aac", ".opus", ".wma", ".aiff", ".aif"}
    PLAYLIST_EXT = {".m3u", ".m3u8", ".pls"}

    def __init__(self):
        self.logger = logging.getLogger("PlexSync.FileSystemProvider")
        mgr = get_manager()
        self.config_mgr = mgr.get_config_manager()
        self.path = Path(self.config_mgr.path).resolve()
        self.playlist_path = Path(self.config_mgr.playlist_path).resolve() if self.config_mgr.playlist_path else self.path
        self.status_mgr = mgr.get_status_manager()
        self._media_files = []
        self._playlist_title_map: dict[str, Path] = {}
        self.deferred_tracks = []

        self.id3_handler = ID3Handler(tagging_policy=self.config_mgr.to_dict())
        self.vorbis_handler = VorbisHandler()
        self._handlers = [self.id3_handler, self.vorbis_handler]

    # ------------------------------
    # Core Handler Dispatch
    # ------------------------------
    def _get_handler(self, audio_file: FileType) -> AudioTagHandler | None:
        """Determine the appropriate handler for the given audio file."""
        return next((handler for handler in self._handlers if handler.can_handle(audio_file)), None)

    # ------------------------------
    # Phase 1: File Discovery (Scanning)
    # ------------------------------
    def scan_media_files(self) -> None:
        """Scan configured paths for audio and playlist files without duplication or scope leakage."""
        self._media_files = []

        audio_root = self.path
        playlist_root = self.playlist_path

        scanned_files: set[Path] = set()
        bar = self.status_mgr.start_phase("Scanning media files", total=None)

        # Track whether playlist_root is fully covered by audio_root
        playlist_scanned_during_audio = playlist_root.is_relative_to(audio_root)

        # Scan audio_root for audio and playlists (if they fall within playlist_root)
        for file_path in audio_root.rglob("*"):
            resolved = file_path.resolve()
            if not file_path.is_file() or resolved in scanned_files:
                continue
            scanned_files.add(resolved)

            ext = file_path.suffix.lower()
            if ext in self.AUDIO_EXT:
                self._media_files.append(resolved)
            if ext in self.PLAYLIST_EXT and file_path.is_relative_to(playlist_root):
                self._media_files.append(resolved)

            bar.update()

        # If playlist_root wasn't already covered, scan it independently (playlists only)
        if not playlist_scanned_during_audio:
            for file_path in playlist_root.rglob("*"):
                resolved = file_path.resolve()
                if not file_path.is_file() or resolved in scanned_files:
                    continue
                scanned_files.add(resolved)

                ext = file_path.suffix.lower()
                if ext in self.PLAYLIST_EXT:
                    self._media_files.append(file_path)

                bar.update()

        bar.close()

        self.logger.info(f"Found {len(self.get_tracks())} audio files")
        self.logger.info(f"Found {len(self._get_playlist_paths())} playlist files")

    def get_tracks(self) -> List[Path]:
        """Return all discovered audio files."""
        return [t for t in self._media_files if t.suffix.lower() in self.AUDIO_EXT]

    def _get_playlist_paths(self) -> List[Path]:
        """Return all discovered playlist files."""
        return [p for p in self._media_files if p.suffix.lower() in self.PLAYLIST_EXT]

    def get_playlists(self, title: str | None = None, path: Path | str | None = None) -> List[Playlist]:
        """
        Return all discovered Playlist objects, optionally filtered by title or resolved path.
        Note: Only one of 'title' or 'path' may be provided. If both are provided, a ValueError is raised.
        """
        if title and path:
            raise ValueError("Only one of 'title' or 'path' may be provided to get_playlists.")
        playlist_paths = self._get_playlist_paths()

        if title:
            matched_path = self._playlist_title_map.get(title)
            playlist_paths = [matched_path] if matched_path else []

        elif path:
            resolved = Path(path).resolve()
            playlist_paths = [p for p in playlist_paths if p.resolve() == resolved]

        playlists = []
        for p in playlist_paths:
            playlist = self.read_playlist_metadata(p)
            if playlist:
                playlists.append(playlist)

        return playlists

    # ------------------------------
    # Phase 2: Metadata Access and Update
    # ------------------------------
    def read_track_metadata(self, file_path: Path | str) -> AudioTag | None:
        audio_file = self._open_audio_file(file_path)
        if not audio_file:
            return None

        handler = self._get_handler(audio_file)
        if not handler:
            self.logger.warning(f"No supported metadata found for {file_path}")
            return None

        tag, raw_ratings = handler.read_tags(audio_file)
        if raw_ratings:
            # only raw, no normalized any more
            self.deferred_tracks.append(
                {
                    "track": tag,
                    "handler": handler,
                    "raw": raw_ratings,
                }
            )

        self.logger.debug(f"Successfully read metadata for {file_path}")
        return tag

    def update_track_metadata(self, file_path: Path | str, audio_tag: AudioTag | None = None, rating: Rating | None = None) -> FileType | None:
        """Update metadata and/or rating in the audio file."""
        file_path = Path(file_path)
        if not file_path.exists():
            self.logger.error(f"File not found: {file_path}")
            return None

        audio_file = self._open_audio_file(file_path)
        if not audio_file:
            self.logger.warning(f"Failed to open file for metadata update: {file_path}")
            return None

        handler = self._get_handler(audio_file)
        if not handler:
            self.logger.warning(f"Cannot update metadata for unsupported format: {file_path}")
            return None

        if audio_tag or rating is not None:
            updated_file = handler.apply_tags(audio_file, audio_tag, rating)
            if self._save_audio_file(updated_file):
                self.logger.info(f"Successfully updated metadata for file: {file_path}")
                return updated_file
        return None

    def finalize_scan(self) -> List[AudioTag]:
        """Finalize the scan by letting handlers resolve strategies and processing deferred tracks."""
        # give each handler a chance to configure conflict strategy / infer scales
        for handler in self._handlers:
            handler.finalize_rating_strategy(self.deferred_tracks)

        resolved_tracks: List[AudioTag] = []
        total = len(self.deferred_tracks)
        bar = self.status_mgr.start_phase("Resolving rating conflicts", total=total) if total > 100 else None

        for entry in self.deferred_tracks:
            track = entry["track"]
            handler = entry["handler"]
            raw = entry["raw"]

            self.logger.debug(f"Resolving deferred rating for {track.artist} | {track.album} | {track.title}")
            # new lifecycle: normalize & resolve from raw only
            rating = handler.resolve_rating(raw, track)
            track.rating = rating

            # write back whichever rating (or unrated) was chosen
            self.update_track_metadata(track.file_path, rating=track.rating)
            resolved_tracks.append(track)

            bar.update() if bar else None

        bar.close() if bar else None

        return resolved_tracks

    # ------------------------------
    # Phase 3: Playlist Operations
    # ------------------------------
    # TODO: dry mode should enforced in the player - not the handler
    def create_playlist(self, title: str, is_extm3u: bool = False) -> Playlist:
        """Create a new M3U playlist file."""
        if self.config_mgr.dry:
            self.logger.info(f"Dry run enabled. Playlist creation of {title} skipped.")
            return None

        playlist_path = self.playlist_path / f"{title}.m3u"

        # Ensure the folder exists
        try:
            playlist_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to create directory for playlist {playlist_path}: {e}")
            return None

        try:
            with playlist_path.open("w", encoding="utf-8") as file:
                if is_extm3u:
                    file.write("#EXTM3U\n")
                    file.write(f"#PLAYLIST:{title}\n")
            self.logger.info(f"Created playlist: {playlist_path}")
        except Exception as e:
            self.logger.error(f"Failed to create playlist {playlist_path}: {e}")
            return None

        return self.read_playlist_metadata(str(playlist_path))

    def read_playlist_metadata(self, playlist_path: Path | str) -> Playlist | None:
        playlist_path = Path(playlist_path).resolve()
        title = playlist_path.stem
        is_extm3u = False

        file = self._open_playlist(playlist_path)
        if not file:
            self.logger.error(f"Failed to open playlist file: {playlist_path}")
            return None
        with file:
            for line in file:
                line = line.strip()
                if line.startswith("#EXTM3U"):
                    is_extm3u = True
                elif not is_extm3u:
                    break
                elif line.startswith("#PLAYLIST:"):
                    title = line.split(":", 1)[1].strip()
                    break

        title = self._get_playlist_title(playlist_path, title)
        self._playlist_title_map[title] = playlist_path

        playlist = Playlist(name=title, ID=str(playlist_path).lower())
        playlist.file_path = str(playlist_path)
        playlist.is_extm3u = is_extm3u
        return playlist

    def get_tracks_from_playlist(self, playlist_path: Path | str) -> List[Path]:
        """Retrieve resolved track paths from a playlist file, scoped to self.path."""
        playlist_path = Path(playlist_path)
        tracks = []

        with self._open_playlist(playlist_path) as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                candidate = Path(line)
                if candidate.is_absolute():
                    candidate_path = candidate.resolve()
                else:
                    candidate_path = (playlist_path.parent / candidate).resolve()

                # Must exist and be within root path
                if candidate_path.exists() and candidate_path.is_relative_to(self.path):
                    tracks.append(candidate_path)
                else:
                    self.logger.debug(f"Ignored invalid or out-of-scope track in playlist: {candidate_path}")
        return tracks

    def add_track_to_playlist(self, playlist_path: str, track: AudioTag, is_extm3u: bool = False) -> None:
        """Add a track to a playlist."""
        playlist_path = Path(playlist_path)
        try:
            # Use playlist_path.parent for relativity, not self.path
            if not Path(track.file_path).is_relative_to(self.path):
                self.logger.debug(f"Track path {track.file_path} is outside the audio root {self.path}; skipping add")
                return

            lines = []
            if is_extm3u:
                duration = int(track.duration) if track.duration > 0 else -1
                lines.append(f"#EXTINF:{duration},{track.artist} - {track.title}\n")
            lines.append(f"{relpath(Path(track.file_path), playlist_path.parent)!s}\n")
            with playlist_path.open("a", encoding="utf-8") as file:
                file.writelines(lines)
            self.logger.info(f"Added track {track} to playlist {playlist_path}")
        except Exception as e:
            self.logger.debug(f"Failed to add track to playlist {playlist_path}: {e}")

    def remove_track_from_playlist(self, playlist_path: str | Path, track: Path) -> None:
        """Remove a track from a playlist."""
        playlist_path = Path(playlist_path)
        try:
            lines = []
            # Use playlist_path.parent for relativity, not self.path
            track_relative = track.relative_to(playlist_path.parent) if track.is_absolute() else track
            track_absolute = track.resolve()

            # Normalize to POSIX for robust comparison
            track_relative_posix = track_relative.as_posix()
            track_absolute_posix = track_absolute.as_posix()

            with playlist_path.open("r", encoding="utf-8") as file:
                for line in file:
                    line_stripped = line.strip()
                    line_posix = Path(line_stripped).as_posix()
                    # Skip lines that match either the relative or absolute path of the track (normalized)
                    if line_posix != track_relative_posix and line_posix != track_absolute_posix:
                        lines.append(line_stripped + "\n")

            with playlist_path.open("w", encoding="utf-8") as file:
                file.writelines(lines)

            self.logger.info(f"Removed track {track} from playlist {playlist_path}")
        except Exception as e:
            self.logger.error(f"Failed to remove track from playlist {playlist_path}: {e}")

    # ------------------------------
    # Utility (Low-Level Helpers)
    # ------------------------------
    def _open_audio_file(self, file_path: Path | str) -> FileType | None:
        """Helper function to open an audio file using mutagen."""
        try:
            audio_file = mutagen.File(file_path, easy=False)
            if not audio_file:
                self.logger.warning(f"Unsupported audio format for file: {file_path}")
                raise ValueError(f"Unsupported audio format: {file_path}")
            return audio_file
        except Exception as e:
            self.logger.error(f"Error opening file {file_path}: {e}", exc_info=True)
            return None

    def _save_audio_file(self, audio_file: FileType) -> bool:
        """Helper function to save changes to an audio file."""
        if self.config_mgr.dry:
            self.logger.info(f"Dry run enabled. Changes to {audio_file.filename} will not be saved.")
            return True  # Simulate a successful save

        try:
            if isinstance(audio_file, ID3FileType):
                audio_file.save(v2_version=3)
            else:
                audio_file.save()
            self.logger.info(f"Successfully saved changes to {audio_file.filename}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save file {audio_file.filename}: {e}", exc_info=True)
            return False

    def _open_playlist(self, path: Path, mode: str = "r", encoding: str = "utf-8"):
        try:
            if "r" in mode and not path.exists():
                raise FileNotFoundError(f"Playlist file not found: {path}")
            return path.open(mode, encoding=encoding)
        except Exception as e:
            self.logger.error(f"Failed to open playlist file {path} with mode '{mode}': {e}")
            return None

    def _get_playlist_title(self, path: Path, base_title: str | None) -> str:
        candidate = base_title
        rel_path = path.relative_to(self.playlist_path)
        folders = list(reversed(rel_path.parts[:-1]))  # exclude file

        parts = [base_title]
        while candidate in self._playlist_title_map and self._playlist_title_map[candidate] != path:
            if not folders:
                self.logger.warning(f"Could not disambiguate duplicate playlist title: {base_title}")
                break
            parts.insert(0, folders.pop(0))
            candidate = ".".join(parts)

        return candidate
