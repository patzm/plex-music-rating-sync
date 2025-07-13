#!/usr/bin/env python3
import locale
import logging
import time
from datetime import timedelta
from typing import Callable, List

from manager import get_manager
from manager.config_manager import PlayerType, SyncItem
from MediaPlayer import FileSystem, MediaMonkey, MediaPlayer, Plex
from ratings import Rating, RatingScale
from sync_items import AudioTag
from sync_pair import MatchThreshold, PlaylistPair, SyncState, TrackPair
from ui.help import ShowHelp
from ui.prompt import UserPrompt


class PlexSync:
    def __init__(self) -> None:
        self.logger = logging.getLogger("PlexSync")
        mgr = get_manager()
        self.config_mgr = mgr.get_config_manager()
        self.stats_mgr = mgr.get_stats_manager()
        self.status_mgr = mgr.get_status_manager()

        self.sync_pairs: list[TrackPair] = []
        self.prompt = UserPrompt()

        self._user_prompt_templates = {
            "sync": "Sync now: {desc} from {source} to {destination}",
            "filter": "Change sync filter",
            "manual": "Manually resolve rating conflicts",
            "details": "View track pair details",
            "cancel": "Cancel and exit sync",
        }
        self.track_filter = {
            "include_unrated": True,
            "quality": None,
            "reverse": False,
            "sync_conflicts": True,
        }

        try:
            self.source_player = self._create_player(self.config_mgr.source)
            self.destination_player = self._create_player(self.config_mgr.destination)
        except ValueError:
            exit(1)
        self.start_time = time.time()

    @property
    def conflicts(self) -> list[TrackPair]:
        return [p for p in self.sync_pairs if p.sync_state == SyncState.CONFLICTING]

    @property
    def unrated(self) -> list[TrackPair]:
        return [p for p in self.sync_pairs if p.sync_state == SyncState.NEEDS_UPDATE]

    def _create_player(self, player_type: str) -> MediaPlayer:
        player_map = {PlayerType.PLEX: Plex, PlayerType.MEDIAMONKEY: MediaMonkey, PlayerType.FILESYSTEM: FileSystem}

        if player_type not in player_map:
            self.logger.error(f"Invalid player type: {player_type}")
            self.logger.error(f"Supported players: {', '.join(player_map.keys())}")
            raise ValueError(f"Invalid player type: {player_type}")

        player = player_map[player_type]()
        player.dry_run = self.config_mgr.dry
        return player

    def sync(self) -> None:
        for player in [self.source_player, self.destination_player]:
            player.connect()

        for sync_item in self.config_mgr.sync:
            if sync_item == SyncItem.TRACKS:
                self.logger.info(f"Starting to sync track ratings from {self.source_player.name()} to {self.destination_player.name()}")
                self.sync_tracks()
            elif sync_item == SyncItem.PLAYLISTS:
                self.logger.info(f"Starting to sync playlists from {self.source_player.name()} to {self.destination_player.name()}")
                self.sync_playlists()
            else:
                raise ValueError(f"Invalid sync item selected: {sync_item}")

    def sync_tracks(self) -> None:
        tracks = self.source_player.search_tracks(key="rating", value=True)
        self.stats_mgr.increment("tracks_processed", len(tracks))

        if not tracks:
            self.logger.warning("No tracks found")
            return

        self.logger.info(f"Attempting to match {len(tracks)} tracks")
        self.sync_pairs = self._match_tracks(tracks)

        if not self.conflicts and not self.unrated:
            print("All tracks are up to date. Nothing to sync.")
            return

        selected_pairs = self._prompt_user_action()
        if selected_pairs:
            if self.config_mgr.dry:
                print("[DRY RUN] No changes will be written.")
            self._sync_ratings(selected_pairs)

    def _match_tracks(self, tracks: List[AudioTag]) -> List[TrackPair]:
        bar = self.status_mgr.start_phase(f"Matching tracks from {self.source_player} to {self.destination_player}", total=len(tracks))
        sync_pairs = []
        for track in tracks:
            pair = TrackPair(self.source_player, self.destination_player, track)
            sync_pairs.append(pair)
            pair.match()
            bar.set_description_str(self._get_match_summary())
            bar.update()
        bar.close()
        return sync_pairs

    def _sync_ratings(self, pairs: List[TrackPair]) -> None:
        if not pairs:
            print("No applicable tracks to update.")
            return

        source = pairs[0].source_player.name()
        destination = pairs[0].destination_player.name()
        label = f"{source} → {destination}"
        track_count = len(pairs)
        scope_summary = self._describe_sync(self.track_filter)

        if self.config_mgr.dry:
            print("[DRY RUN] No changes will be written.")

        self.logger.info(msg := f"Syncing {track_count} tracks ({scope_summary}) using direction: {label}")
        if self.logger.getEffectiveLevel() > logging.INFO:
            print(msg)

        bar = self.status_mgr.start_phase(f"Syncing ratings ({label})", total=track_count)
        for pair in pairs:
            pair.sync()
            bar.update()
        bar.close()

    def _filter_sync_pairs(self) -> list[TrackPair]:
        pairs = self.sync_pairs
        if self.track_filter.get("reverse"):
            pairs = [p.reversed() for p in pairs]

        filter_fn = self._get_track_filter(
            quality=self.track_filter.get("quality"),
            include_needs_update=self.track_filter.get("include_unrated", True),
            include_conflicts=self.track_filter.get("sync_conflicts", True),
            include_unmatched=False,
        )

        return [p for p in pairs if filter_fn(p)]

    def sync_playlists(self) -> None:
        print(f"Discovering playlists from {self.source_player.name()}")
        playlists = self.source_player.search_playlists("all")
        if not playlists:
            self.logger.warning("No playlists found")
            return

        playlist_pairs = [PlaylistPair(self.source_player, self.destination_player, pl) for pl in playlists if not pl.is_auto_playlist]
        self.stats_mgr.increment("playlists_processed", len(playlist_pairs))

        if self.config_mgr.dry:
            self.logger.info("Running a DRY RUN. No changes will be propagated!")

        self.logger.info(f"Matching {self.source_player.name()} playlists with {self.destination_player.name()}")

        # Start playlist matching phase
        bar = None
        for pair in playlist_pairs:
            if bar is None:
                bar = self.status_mgr.start_phase("Matching playlists", total=len(playlist_pairs))
            pair.match()
            bar.update()
        bar.close() if bar else None

        # Start playlist sync phase
        bar = None
        for pair in playlist_pairs:
            if bar is None:
                bar = self.status_mgr.start_phase("Syncing playlists", total=len(playlist_pairs))
            pair.sync()
            bar.update()
        bar.close() if bar else None

    def _describe_sync(self, track_filter: dict) -> str:
        include_unrated = track_filter.get("include_unrated", True)
        sync_conflicts = track_filter.get("sync_conflicts", True)
        quality = track_filter.get("quality")  # None = no minimum

        parts = []

        if include_unrated:
            parts.append("unrated")

        if sync_conflicts:
            q_desc = "conflicting" if quality is None else f"{quality!s}+ conflicting"
            parts.append(q_desc)

        return " and ".join(parts) + " tracks" if parts else "no tracks"

    def _prompt_user_action(self) -> List[TrackPair] | None:
        while True:
            self._build_user_prompt()
            menu_options = [UserPrompt.MenuOption(key=k, label=v) for k, v in self.user_prompt_options.items()]
            selected_key = self.prompt.choice("How would you like to proceed?", menu_options, help_text=ShowHelp.SyncOptions)

            match selected_key:
                case "sync":
                    return self._filter_sync_pairs()

                case "filter":
                    self._prompt_filter_sync()
                    continue

                case "cancel":
                    print("Sync tracks canceled.")
                    return None

                case "manual":
                    return self._resolve_conflicts_manually()

                case "details":  # pragma: no branch
                    self._prompt_detailed_view()
                    continue

    def _build_user_prompt(self) -> None:
        desc = self._describe_sync(self.track_filter)
        source = self.destination_player.name() if self.track_filter["reverse"] else self.source_player.name()
        destination = self.source_player.name() if self.track_filter["reverse"] else self.destination_player.name()

        self.user_prompt_options = {}

        if self.sync_pairs:
            self.user_prompt_options["sync"] = self._user_prompt_templates["sync"].format(desc=desc, source=source, destination=destination)

        self.user_prompt_options["filter"] = self._user_prompt_templates["filter"]

        if self.conflicts:
            self.user_prompt_options["manual"] = self._user_prompt_templates["manual"]

        if self.unrated or self.conflicts:
            desc = self._describe_sync(self.track_filter)
            self.user_prompt_options["details"] = self._user_prompt_templates["details"].format(desc=desc)

        self.user_prompt_options["cancel"] = self._user_prompt_templates["cancel"]

    def _prompt_filter_sync(self) -> None:
        while True:
            reverse = self.track_filter.get("reverse", False)
            include_unrated = self.track_filter.get("include_unrated", True)
            syncing_conflicts = self.track_filter.get("sync_conflicts", True)  # new explicit toggle

            source = self.destination_player.name() if reverse else self.source_player.name()
            destination = self.source_player.name() if reverse else self.destination_player.name()

            options = {
                "reverse": f"Reverse sync from {destination} to {source}",
                "conflicts": f"{'Disable' if syncing_conflicts else 'Enable'} syncing of tracks with rating conflicts",
                "unrated": f"{'Disable' if include_unrated else 'Enable'} syncing of unrated tracks from {destination}",
                "quality": "Change minimum match quality",
                "cancel": "Cancel and return to the previous menu",
            }

            menu_options = [UserPrompt.MenuOption(key=k, label=v) for k, v in options.items()]

            filtered_count = len(self._filter_sync_pairs())
            summary = self._describe_sync(self.track_filter)
            prompt_label = f"Adjust sync filter settings.\nCurrently syncing: {summary} ({filtered_count} track{'s' if filtered_count != 1 else ''})."
            selected_key = self.prompt.choice(prompt_label, menu_options, help_text=ShowHelp.SyncFilter)

            if selected_key == "reverse":
                self.track_filter["reverse"] = not reverse

            elif selected_key == "conflicts":
                self.track_filter["sync_conflicts"] = not syncing_conflicts

            elif selected_key == "unrated":
                self.track_filter["include_unrated"] = not include_unrated

            elif selected_key == "quality":
                selected = self._prompt_select_quality_threshold()
                self.track_filter["quality"] = selected

            elif selected_key == "cancel":  # pragma: no branch
                self.logger.info(f"Updated track filter: {self.track_filter}")
                return

    def _prompt_select_quality_threshold(self) -> MatchThreshold | None:
        thresholds = [
            ("All", None),
            ("Perfect", MatchThreshold.PERFECT_MATCH),
            ("Good", MatchThreshold.GOOD_MATCH),
            ("Poor", MatchThreshold.POOR_MATCH),
        ]

        current = self.track_filter.get("quality")
        current_label = current.name if current else "All"

        menu_options = []
        key_to_threshold = {}
        for label, threshold in thresholds:
            filter_fn = self._get_track_filter(
                quality=threshold,
                include_unmatched=False,
                include_needs_update=self.track_filter.get("include_unrated", True),
                include_conflicts=self.track_filter.get("sync_conflicts", True),
            )
            count = sum(1 for p in self.sync_pairs if filter_fn(p))
            key = label.lower()
            menu_options.append(UserPrompt.MenuOption(key=key, label=f"{label} ({count})"))
            key_to_threshold[key] = threshold

        selected_key = self.prompt.choice(f"Select minimum match quality (currently {current_label}):", menu_options, help_text=ShowHelp.MatchQuality)
        return key_to_threshold[selected_key]

    def _resolve_conflicts_manually(self) -> list[TrackPair]:
        resolved: list[TrackPair] = []

        for i, pair in enumerate(self.conflicts, 1):
            src_desc = f"{pair.source_player.name():<20}: ({pair.source.title}) - Rating: {pair.rating_source.to_display()}"
            dst_desc = f"{pair.destination_player.name():<20}: ({pair.destination.title}) - Rating: {pair.rating_destination.to_display()}"

            manual_options = {
                "src_to_dst": src_desc,
                "dst_to_src": dst_desc,
                "manual": "Enter a new rating",
                "skip": "Skip this track",
                "cancel": "Cancel conflict resolution",
            }
            menu_options = [UserPrompt.MenuOption(key=k, label=v) for k, v in manual_options.items()]

            print(f"Resolving conflict {i} of {len(self.conflicts)}:")
            selected_key = self.prompt.choice("Choose how to resolve this conflict:", menu_options, help_text=ShowHelp.ManualConflictResolution)

            if selected_key == "src_to_dst":
                resolved.append(pair)

            elif selected_key == "dst_to_src":
                resolved.append(pair.reversed())

            elif selected_key == "manual":
                rating_input = self.prompt.text(
                    "Enter a new rating (0-5, half-star increments):",
                    validator=lambda val: Rating.validate(val, scale=RatingScale.ZERO_TO_FIVE) is not None,
                    help_text="Allowed values: 0, 0.5, 1, 1.5, ..., 5",
                )
                if rating_input is not None:
                    rating = Rating(rating_input, scale=RatingScale.ZERO_TO_FIVE)

                    # Manually inject rating into both directions for consistency
                    reverse_pair = pair.reversed()
                    pair.source.rating = rating
                    reverse_pair.source.rating = rating

                    resolved.append(pair)
                    resolved.append(reverse_pair)

            elif selected_key == "skip":
                continue

            elif selected_key == "cancel":  # pragma: no branch
                print("Manual resolution canceled.")
                break

        return resolved

    def _prompt_detailed_view(self) -> None:
        scope_options = {
            "all": "All discovered tracks",
            "unrated": "Only unrated destination tracks",
            "conflicting": "Only tracks with rating conflicts",
        }
        current_scope = "all"

        while True:
            scope_label = scope_options[current_scope]
            prompt_label = f"View details: {scope_label}"
            cancel_option = UserPrompt.MenuOption(key="cancel", label="Cancel and return to the previous menu")

            menu_options = []
            # Add scope switch options
            for key, desc in scope_options.items():
                if key != current_scope:
                    menu_options.append(UserPrompt.MenuOption(key=f"switch_{key}", label=f"Switch to: {desc}"))

            # Get scoped categories
            category_options, filters = self._get_match_display_options(current_scope)
            menu_options.extend(category_options)
            menu_options.append(cancel_option)

            selection = self.prompt.choice(prompt_label, menu_options)

            if selection == "cancel":
                return

            elif selection.startswith("switch_"):
                new_scope = selection.replace("switch_", "")
                if new_scope in scope_options:
                    current_scope = new_scope
                continue

            label = selection
            filter_fn = filters[label]
            filtered_pairs = [p for p in self.sync_pairs if filter_fn(p)]

            self._display_trackpair_list(filtered_pairs, f"{label} — {scope_label}")

    def _get_match_display_options(self, scope: str) -> tuple[list, dict[str, Callable[[TrackPair], bool]]]:
        def in_scope(p: TrackPair) -> bool:
            if scope == "all":
                return True
            if scope == "unrated":
                return p.sync_state == SyncState.NEEDS_UPDATE
            if scope == "conflicting":
                return p.sync_state == SyncState.CONFLICTING
            return False

        label_filters = {
            "Perfect Matches": lambda p: in_scope(p) and p.quality == MatchThreshold.PERFECT_MATCH,
            "Good Matches": lambda p: in_scope(p) and p.quality == MatchThreshold.GOOD_MATCH,
            "Poor Matches": lambda p: in_scope(p) and p.quality == MatchThreshold.POOR_MATCH,
        }

        if scope == "all":
            label_filters["Unmatched"] = lambda p: p.is_unmatched

        options = []
        for label, fn in label_filters.items():
            count = sum(1 for p in self.sync_pairs if fn(p))
            if count > 0:
                options.append(UserPrompt.MenuOption(key=label, label=f"{label} ({count})"))

        return options, label_filters

    def _display_trackpair_list(self, track_pairs: list[TrackPair], label: str) -> None:  # pragma: no cover
        if not track_pairs:
            print(f"No tracks matched the selected category: {label}")
            return

        print(f"\n{label}: {len(track_pairs)} tracks")
        header_line = AudioTag.DISPLAY_HEADER

        for i, pair in enumerate(track_pairs, 1):
            if (i - 1) % 100 == 0:
                print("-" * 137)
                print(header_line)
                print("-" * 137)

            print(pair.source.details(pair.source_player))
            if pair.destination:
                print(pair.destination.details(pair.destination_player))
            else:
                print("(No destination track matched)")
            print("-" * 137)

            if i % 100 == 0 and i != len(track_pairs):
                cont = self.prompt.confirm_continue(f"[{i}/{len(track_pairs)}] — Press Enter to continue or 'q' to quit: ")
                if not cont:
                    break

    def _get_track_filter(
        self, *, quality: MatchThreshold = None, include_unmatched: bool = False, include_needs_update: bool = True, include_conflicts: bool = True
    ) -> Callable[[TrackPair], bool]:
        """
        Returns a filter function for TrackPairs based on:
        - Whether to include unmatched (ERROR or UNKNOWN) tracks
        - Whether to include unrated tracks (NEEDS_UPDATE)
        - Quality level for CONFLICTING tracks
        - Whether quality comparison is exact or threshold-based
        """

        def fn(pair: TrackPair) -> bool:
            if not include_unmatched and pair.is_unmatched:
                return False

            if pair.sync_state == SyncState.NEEDS_UPDATE:
                return include_needs_update and (quality is None or pair.has_min_quality(quality))

            if pair.sync_state == SyncState.CONFLICTING:
                return include_conflicts and (quality is None or pair.has_min_quality(quality))

            return False

        return fn

    def _get_match_summary(self) -> str:  # pragma: no cover
        """Generate a summary of match quality statistics."""
        return (
            f"100%: {self.stats_mgr.get('perfect_matches')} | "
            f"Good: {self.stats_mgr.get('good_matches')} | "
            f"Poor: {self.stats_mgr.get('poor_matches')} | "
            f"None: {self.stats_mgr.get('no_matches')}"
        )

    def print_summary(self) -> None:  # pragma: no cover
        """Print and log the sync summary."""
        elapsed = time.time() - self.start_time
        elapsed_time = str(timedelta(seconds=int(elapsed)))

        summary_lines = [
            "\nSync Summary:",
            "-" * 50,
            f"Total time: {elapsed_time}",
        ]

        if SyncItem.TRACKS in self.config_mgr.sync:
            summary_lines.append("Tracks:")
            summary_lines.append(f"- Processed: {self.stats_mgr.get('tracks_processed')}")
            summary_lines.append(f"- Matched: {self.stats_mgr.get('tracks_matched')}")
            summary_lines.append(f"- Updated: {self.stats_mgr.get('tracks_updated')}")
            summary_lines.append(f"- Conflicts: {self.stats_mgr.get('tracks_conflicts')}")

            summary_lines.append("\nMatch Quality:")
            summary_lines.append(f"- Perfect matches (100%): {self.stats_mgr.get('perfect_matches')}")
            summary_lines.append(f"- Good matches (80-99%): {self.stats_mgr.get('good_matches')}")
            summary_lines.append(f"- Poor matches (30-79%): {self.stats_mgr.get('poor_matches')}")
            summary_lines.append(f"- No matches (<30%): {self.stats_mgr.get('no_matches')}")
            if self.config_mgr.log == "DEBUG":
                summary_lines.append(f"- Cache hits: {self.stats_mgr.get('cache_hits')}")

        if SyncItem.PLAYLISTS in self.config_mgr.sync:
            summary_lines.append("\nPlaylists:")
            summary_lines.append(f"- Processed: {self.stats_mgr.get('playlists_processed')}")
            summary_lines.append(f"- Matched: {self.stats_mgr.get('playlists_matched')}")
            summary_lines.append(f"- Created: {self.stats_mgr.get('playlists_created')}")
            summary_lines.append(f"- Updated: {self.stats_mgr.get('playlists_updated')}")

        if self.config_mgr.dry:
            summary_lines.append("\nThis was a DRY RUN - no changes were actually made.")
        summary_lines.append("-" * 50)

        # Log the summary to the debug log
        for line in summary_lines:
            self.logger.debug(line)

        # Print the summary only if the logger level is not DEBUG
        if self.config_mgr.log != logging.DEBUG:
            for line in summary_lines:
                print(line)


if __name__ == "__main__":
    locale.setlocale(locale.LC_ALL, "")

    get_manager().initialize()
    config_mgr = get_manager().get_config_manager()
    cache_mgr = get_manager().get_cache_manager()
    if config_mgr.clear_cache:
        cache_mgr.invalidate()

    sync_agent = PlexSync()
    sync_agent.sync()
    sync_agent.print_summary()

    cache_mgr.cleanup()
