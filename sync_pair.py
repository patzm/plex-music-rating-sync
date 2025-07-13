import abc
import logging
from enum import Enum, IntEnum, auto
from typing import List, Tuple

import numpy as np
from fuzzywuzzy import fuzz

from manager import get_manager
from MediaPlayer import MediaPlayer
from ratings import Rating
from sync_items import AudioTag, Playlist


class SyncState(Enum):
    UNKNOWN = auto()
    UP_TO_DATE = auto()
    NEEDS_UPDATE = auto()
    CONFLICTING = auto()
    ERROR = auto()


class MatchThreshold(IntEnum):
    MINIMUM_ACCEPTABLE = 30
    POOR_MATCH = MINIMUM_ACCEPTABLE
    GOOD_MATCH = 80
    PERFECT_MATCH = 100

    def __str__(self) -> str:
        return self.name.replace("_MATCH", "").title()


class SyncPair(abc.ABC):
    source = None
    destination = None
    sync_state = SyncState.UNKNOWN

    def __init__(self, source_player: MediaPlayer, destination_player: MediaPlayer) -> None:
        mgr = get_manager()
        self.stats_mgr = mgr.get_stats_manager()
        self.cache_mgr = mgr.get_cache_manager()
        self.source_player: MediaPlayer = source_player
        self.destination_player: MediaPlayer = destination_player

    @abc.abstractmethod
    def match(self, *args, **kwargs) -> bool:
        """Tries to find a match on the destination player that matches the source replica as good as possible"""

    @abc.abstractmethod
    def similarity(self, candidate: AudioTag) -> float:
        """Determines the similarity of the source replica with the candidate replica"""

    @abc.abstractmethod
    def sync(self, force: bool = False) -> bool:
        """Synchronizes the source and destination replicas"""


class TrackPair(SyncPair):
    rating_source: Rating | None = None
    rating_destination: Rating | None = None
    score: int | None = None

    def __init__(self, source_player: MediaPlayer, destination_player: MediaPlayer, source_track: AudioTag) -> None:
        super(TrackPair, self).__init__(source_player, destination_player)
        self.logger = logging.getLogger("PlexSync.TrackPair")
        self.source = source_track

    @property
    def quality(self) -> MatchThreshold | None:
        if self.score is None or self.sync_state in {SyncState.ERROR, SyncState.UNKNOWN}:
            return None
        if self.score >= MatchThreshold.PERFECT_MATCH:
            return MatchThreshold.PERFECT_MATCH
        if self.score >= MatchThreshold.GOOD_MATCH:
            return MatchThreshold.GOOD_MATCH
        if self.score >= MatchThreshold.POOR_MATCH:
            return MatchThreshold.POOR_MATCH
        return None

    @property
    def is_sync_candidate(self) -> bool:
        """Eligible for syncing: either unrated or conflicting."""
        return self.sync_state in {SyncState.NEEDS_UPDATE, SyncState.CONFLICTING}

    @property
    def is_unmatched(self) -> bool:
        """Failed to match due to system or search failure."""
        return self.sync_state in {SyncState.UNKNOWN, SyncState.ERROR}

    def has_min_quality(self, quality: MatchThreshold) -> bool:
        return self.quality is not None and self.quality >= quality

    def _record_match_quality(self, score: int) -> None:
        """Track match quality statistics based on the score."""
        match self.quality:
            case MatchThreshold.PERFECT_MATCH:
                self.stats_mgr.increment("perfect_matches")
            case MatchThreshold.GOOD_MATCH:
                self.stats_mgr.increment("good_matches")
            case MatchThreshold.POOR_MATCH:
                self.stats_mgr.increment("poor_matches")
            case _:
                self.stats_mgr.increment("no_matches")

    def reversed(self) -> "TrackPair":
        """Return a new TrackPair with source and destination roles swapped."""
        reversed_pair = TrackPair(
            source_player=self.destination_player,
            destination_player=self.source_player,
            source_track=self.destination,
        )
        reversed_pair.destination = self.source
        reversed_pair.rating_source = self.rating_destination
        reversed_pair.rating_destination = self.rating_source
        reversed_pair.score = self.score
        reversed_pair.sync_state = self.sync_state
        return reversed_pair

    @staticmethod
    def display_pair_details(category: str, sync_pairs: List["TrackPair"]) -> None:  # pragma: no cover
        """Display track details in a tabular format."""
        if not sync_pairs:
            print(f"\nNo tracks found for {category}.")
            return

        separator = "-" * 137
        print(f"\n{category}:\n{separator}")
        print(AudioTag.DISPLAY_HEADER)
        print(separator)

        for pair in sync_pairs:
            print(pair.source.details(pair.source_player))
            if pair.destination:
                print(pair.destination.details(pair.destination_player))
                print(separator)

    def albums_similarity(self, destination: AudioTag | None = None) -> int:
        """Determines how similar two album names are. It takes into account different conventions for empty album names."""
        if destination is None:
            destination = self.destination
        if self.both_albums_empty(destination=destination):
            return MatchThreshold.PERFECT_MATCH
        else:
            return fuzz.ratio(self.source.album, destination.album)

    def both_albums_empty(self, destination: AudioTag | None = None) -> bool:
        return self.source_player.album_empty(self.source.album) and self.destination_player.album_empty(destination.album)

    def _get_cache_match(self) -> AudioTag | None:
        """Attempt to retrieve a cached match for the current source track."""
        if not self.cache_mgr.match_cache:
            return None

        cached_id, cached_score = self.cache_mgr.get_match(
            self.source.ID,
            source_name=self.source_player.name(),
            dest_name=self.destination_player.name(),
        )

        if not cached_id or cached_score is None:
            return None
        if cached_score < MatchThreshold.GOOD_MATCH:
            return None

        candidates = self.destination_player.search_tracks(key="id", value=cached_id)
        if candidates:
            self.stats_mgr.increment("cache_hits")
            destination_track = candidates[0]

            self.score = cached_score
            return destination_track
        return None

    def _search_candidates(self) -> List[AudioTag]:
        """Search for track candidates matching the source track in the destination player."""
        if not self.source.title:
            self.logger.error(f"Source track has no title: {self.source.file_path}")
            return []
        try:
            candidates = self.destination_player.search_tracks(key="title", value=self.source.title)
            return candidates
        except ValueError as e:
            self.logger.error(f"Search failed for '{self.source.title}.")
            raise e

    def _get_best_match(self, candidates: List[AudioTag], match_threshold: int = MatchThreshold.MINIMUM_ACCEPTABLE) -> Tuple[AudioTag | None, int]:
        """Find the best matching track from a list of candidates based on similarity score."""
        if not candidates:
            return None, 0

        scores = np.array([self.similarity(candidate) for candidate in candidates])
        best_idx = np.argmax(scores)
        best_score = scores[best_idx]

        if best_score < match_threshold:
            self.logger.debug(f"Best candidate score too low: {best_score} < {match_threshold}")
            return None, best_score

        best_match = candidates[best_idx]
        return best_match, best_score

    def _set_cache_match(self, best_match: AudioTag, score: float | None = None) -> None:
        """Store a successful match in the cache along with its score."""
        if not self.cache_mgr.match_cache:
            return

        self.cache_mgr.set_match(self.source.ID, best_match.ID, self.source_player.name(), self.destination_player.name(), score)

    def find_best_match(self, candidates: List[AudioTag] | None = None, match_threshold: int = MatchThreshold.MINIMUM_ACCEPTABLE) -> Tuple[AudioTag | None, int]:
        """Find the best matching track from candidates or by searching."""
        cached_match = self._get_cache_match()
        if cached_match:
            return cached_match, self.score

        if candidates is None:
            candidates = self._search_candidates()

        if not candidates:
            self.logger.warning(f"No candidates found for {self.source}")
            return None, 0

        best_match, best_score = self._get_best_match(candidates, match_threshold)

        if best_match:
            self._set_cache_match(best_match, best_score)

            self.logger.debug(f"Found match with score {best_score} for {self.source} - : - {best_match}")
            if best_score != MatchThreshold.PERFECT_MATCH:
                self.logger.info(f"Found match with score {best_score} for {self.source}: {best_match}")
            if best_score < MatchThreshold.GOOD_MATCH:
                self.logger.debug(f"Source: {self.source}")
                self.logger.debug(f"Best Match: {best_match}")

        return best_match, best_score

    def match(self, candidates: List[AudioTag] | None = None, match_threshold: int = MatchThreshold.MINIMUM_ACCEPTABLE) -> bool:
        """Find matching track on destination player"""
        if self.source is None:
            raise RuntimeError("Source track not set")

        best_match, score = self.find_best_match(candidates, match_threshold)

        if not best_match:
            self.sync_state = SyncState.ERROR
            return False

        self.destination = best_match

        src = self.rating_source = self.source.rating or Rating.unrated()
        dst = self.rating_destination = self.destination.rating or Rating.unrated()

        if src == dst:
            self.sync_state = SyncState.UP_TO_DATE
        elif src and dst.is_unrated:
            self.sync_state = SyncState.NEEDS_UPDATE
        else:
            self.sync_state = SyncState.CONFLICTING
            self.logger.warning(f"Found match with conflicting ratings: {self.source} " f"(Source: {src.to_display()} | " f"Destination: {dst.to_display()})")

        self.score = score
        self._record_match_quality(score)
        self.stats_mgr.increment("tracks_matched")
        return True

    def similarity(self, candidate: AudioTag) -> float:
        """Determines the matching similarity of @candidate with the source query track"""
        # TODO: add path similarity
        scores = np.array(
            [
                fuzz.ratio(self.source.title, candidate.title),
                fuzz.ratio(self.source.artist, candidate.artist),
                MatchThreshold.PERFECT_MATCH if self.source.track == candidate.track else 0,
                self.albums_similarity(destination=candidate),
            ]
        )
        return np.average(scores)

    def sync(self) -> None:
        """Synchronizes the source and destination tracks."""
        self.destination_player.update_rating(self.destination, self.rating_source)
        self.stats_mgr.increment("tracks_updated")


class PlaylistPair(SyncPair):
    source: Playlist
    destination: Playlist | None = None

    def __init__(self, source_player: MediaPlayer, destination_player: MediaPlayer, source_playlist: Playlist) -> None:
        """Initialize playlist pair with consistent naming"""
        super(PlaylistPair, self).__init__(source_player, destination_player)
        self.status_mgr = get_manager().get_status_manager()
        self.logger = logging.getLogger("PlexSync.PlaylistPair")
        self.source = source_playlist

    def match(self) -> bool:
        """Find matching playlist on destination player based on name."""
        matches = self.destination_player.search_playlists("title", self.source.name)
        self.destination = matches[0] if matches else None

        if self.destination is None:
            self.sync_state = SyncState.NEEDS_UPDATE
            self.logger.info(f"Playlist {self.source.name} needs to be created")
            return False

        if not self.source.tracks:
            self.source_player.load_playlist_tracks(self.source)

        if not self.destination.tracks:
            self.destination_player.load_playlist_tracks(self.destination)

        missing = self.destination.missing_tracks(self.source)
        if missing:
            self.sync_state = SyncState.NEEDS_UPDATE
            self.logger.info(f"Found {len(missing)} missing tracks in playlist {self.destination}")
        else:
            self.sync_state = SyncState.UP_TO_DATE
            self.logger.info(f"Playlist {self.source.name} is up to date")

        self.stats_mgr.increment("playlists_matched")
        return True

    def similarity(self, candidate: Playlist) -> float:
        """Determines the similarity of the playlist with a candidate."""
        raise NotImplementedError

    def _create_new_playlist(self, track_pairs: List[TrackPair]) -> bool:
        """Create a new playlist on the destination player with matched tracks."""
        destination_tracks = [pair.destination for pair in track_pairs]
        self.destination = self.destination_player.create_playlist(self.source.name, destination_tracks)
        self.logger.info(f"Created new playlist {self.source.name} with {len(destination_tracks)} tracks")
        self.stats_mgr.increment("playlists_created")
        return True

    def _update_existing_playlist(self, track_pairs: List[TrackPair]) -> bool:
        """Update an existing playlist on the destination player with missing tracks."""
        updates = []
        if len(track_pairs) > 0:
            for pair in track_pairs:
                if not self.destination.has_track(pair.destination):
                    self.logger.debug(f"Track not found in playlist {self.destination}: {pair.destination}")
                    updates.append(pair.destination)

        if updates:
            self.logger.debug(f"Adding {len(updates)} missing tracks to playlist {self.destination}")
            self.destination_player.sync_playlist(self.destination, updates)
            self.stats_mgr.increment("playlists_updated")
            return True
        else:
            self.logger.debug(f"Playlist {self.source.name} is up to date")
            return False

    def _match_tracks(self) -> Tuple[List[TrackPair], List[AudioTag]]:
        """Helper method to match tracks from the source playlist."""
        track_pairs = []
        unmatched = []

        if len(self.source.tracks) == 0:
            self.source_player.load_playlist_tracks(self.source)

        if len(self.source.tracks) > 0:
            bar = None
            if len(self.source.tracks) > 50:
                bar = self.status_mgr.start_phase(f"Matching tracks for playlist '{self.source.name}'", total=len(self.source.tracks))
            for track in self.source.tracks:
                bar.update() if bar else None
                pair = TrackPair(self.source_player, self.destination_player, track)
                pair.match()
                if pair.destination is not None:
                    track_pairs.append(pair)
                else:
                    unmatched.append(track)
            bar.close() if bar else None

        return track_pairs, unmatched

    def sync(self, force: bool = False) -> bool:
        """Non-destructive one-way sync from source to destination."""
        if self.sync_state is SyncState.UP_TO_DATE:
            return True

        self.logger.info(f"Synchronizing playlist {self.source.name}")
        self.logger.debug(f"Source playlist has {len(self.source.tracks)} tracks")

        track_pairs, unmatched = self._match_tracks()

        self.logger.info(f"Matched {len(track_pairs)}/{len(self.source.tracks)} tracks for playlist {self.source.name}")
        if unmatched:
            self.logger.warning(f"Failed to match {len(unmatched)} tracks:")
            for track in unmatched:
                self.logger.warning(f"  - {track}")

        if not track_pairs:
            self.logger.warning(f"No tracks could be matched for playlist {self.source.name}")
            return False

        if self.destination is None:
            return self._create_new_playlist(track_pairs)
        else:
            return self._update_existing_playlist(track_pairs)
