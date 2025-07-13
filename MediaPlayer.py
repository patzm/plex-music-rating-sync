import abc
import getpass
import logging
import time
from pathlib import Path
from typing import Any, List, Tuple

import plexapi.audio
import plexapi.playlist
from fuzzywuzzy import fuzz
from plexapi.exceptions import BadRequest, NotFound
from plexapi.myplex import MyPlexAccount

from filesystem_provider import FileSystemProvider
from manager import get_manager
from manager.config_manager import SyncItem
from ratings import Rating, RatingScale
from sync_items import AudioTag, Playlist

NativePlaylist = Any
NativeTrack = Any
MediaMonkeyPlaylist = Any  # COM SDBPlaylist object
MediaMonkeyTrack = Any  # COM SDBSongData object
PlexPlaylist = Any  # plexapi.playlist.Playlist
PlexTrack = Any  # plexapi.audio.Track

# TODO: itunes
# TODO: add mediamonkey 5
# TODO: add updating Album, Artist, Title, Rating, Track, Genre
# TODO: add setting source of record per attribute


class MediaPlayer(abc.ABC):
    album_empty_alias = ""
    dry_run = False
    rating_scale = None

    def __init__(self):
        self._title_to_id_map = {}

        mgr = get_manager()
        self.config_mgr = mgr.get_config_manager()
        self.cache_mgr = mgr.get_cache_manager()
        self.status_mgr = mgr.get_status_manager()

    def __str__(self) -> int:
        return self.name()

    def __hash__(self) -> int:
        return hash(self.name().lower())

    def __eq__(self, other: "MediaPlayer") -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return other.name().lower() == self.name().lower()

    @staticmethod
    @abc.abstractmethod
    def name() -> str:
        raise NotImplementedError("Subclasses must implement this method.")

    def album_empty(self, album: str) -> bool:
        if not isinstance(album, str):
            return False
        return album == self.album_empty_alias

    @abc.abstractmethod
    def connect(self, *args, **kwargs) -> None:
        """Connect to the media player."""

    def search_playlists(self, key: str = "all", value: str | int | None = None, return_native: bool = False) -> List[Playlist]:
        if key not in {"all", "title", "id"}:
            raise ValueError(f"Invalid search key: {key}")
        return self._search_playlists(key, value, return_native)

    def search_tracks(self, key: str, value: bool | str, return_native: bool = False) -> List[AudioTag]:
        """Search tracks and convert to AudioTag format"""
        self.logger.trace(f"Searching {self.name()} for tracks with {key}={value}")
        if not value:
            self.logger.error("Search value cannot be empty")
            raise ValueError("value can not be empty.")

        tracks = self._search_tracks(key, value, return_native)
        return tracks

    def sync_playlist(self, playlist: Playlist, updates: List[Tuple[AudioTag, bool]]) -> None:
        """Sync playlist changes to native format"""
        if not updates:
            self.logger.debug("No updates to sync")
            return

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would sync {len(updates)} changes to playlist '{playlist.name}'")
            return

        self.logger.info(f"Syncing {len(updates)} changes to playlist '{playlist.name}'")
        bar = self.status_mgr.start_phase("Syncing playlist updates", total=len(updates))
        for track, present in updates:
            self.update_playlist(playlist, track, present)
            bar.update()
        bar.close()
        self.logger.debug("Playlist sync completed")

    def create_playlist(self, title: str, tracks: List[AudioTag]) -> Playlist | None:
        """Create a new playlist with the given tracks"""
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would create playlist '{title}' with {len(tracks)} tracks")
            return None

        if not tracks:
            self.logger.warning("No tracks provided for playlist creation")
            return None
        self.logger.info(f"Creating playlist '{title}' with {len(tracks)} tracks")
        playlist = self._create_playlist(title, tracks)
        if playlist:
            self.logger.debug(f"Successfully created playlist '{title}'")
            return playlist
        self.logger.error(f"Failed to create playlist '{title}'")
        return None

    @abc.abstractmethod
    def _update_rating(self, track: AudioTag, rating: Rating) -> None:
        """Player-specific implementation for updating a track's rating."""
        pass

    def update_rating(self, track: AudioTag, rating: Rating) -> None:
        """Updates the rating of the track, unless in dry run"""
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would update rating for {track} to {rating.to_display()}")
            return

        self.logger.debug(f"Updating rating on {self.name()} for {track} to {rating.to_display()}")
        try:
            self._update_rating(track, rating)
        except Exception as e:
            self.logger.error(f"Failed to update rating: {e!s}")
            raise
        self.logger.info(f"Successfully updated rating for {track}")

    def update_playlist(self, playlist: Playlist, track: AudioTag, present: bool) -> None:
        """Updates the playlist by adding or removing a track"""
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would {'add' if present else 'remove'} track from playlist")
            return

        self.logger.info(f"{'Adding' if present else 'Removing'} track '{track}' {'to' if present else 'from'} playlist '{playlist.name}'")

        try:
            if present:
                self._add_track_to_playlist(playlist, track)
            else:
                self._remove_track_from_playlist(playlist, track)
            self.logger.debug(f"Successfully {'added' if present else 'removed'} track")
        except Exception as e:
            self.logger.error(f"Failed to {'add' if present else 'remove'} track: {e!s}")
            raise

    def load_playlist_tracks(self, playlist: Playlist) -> None:
        """Load tracks from a playlist into the Playlist object."""
        native_playlists = self._search_playlists("id", playlist.ID, return_native=True)
        if not native_playlists:
            self.logger.warning(f"Native playlist not found for {playlist.name}")
            return
        native_playlist = native_playlists[0]
        tracks = self._read_native_playlist_tracks(native_playlist)
        bar = None
        if not playlist.is_auto_playlist:
            total_tracks = self._get_native_playlist_track_count(native_playlist)
            if total_tracks > 100:
                bar = self.status_mgr.start_phase(f"Reading tracks from playlist {playlist.name}", total=total_tracks)

            for t in tracks:
                track = self._read_track_metadata(t)
                playlist.tracks.append(track)
                if bar:
                    bar.update()
                    self.logger.debug(f"Reading track {track} from playlist {playlist.name}")

            if bar:
                bar.close()

    def _register_playlist_title(self, title: str, ID: str | int) -> None:
        """Register a playlist title → ID mapping (both directions), enforcing uniqueness across this player."""
        key = title.lower()
        existing = self._title_to_id_map.get(key)

        if existing is not None and existing != ID:
            raise ValueError(
                f"Ambiguous playlist title '{title}' in {self.name()} maps to multiple IDs: '{existing}' and '{ID}'. "
                f"{self.name()} requires playlist titles to be globally unique per player."
            )

        self._title_to_id_map[key] = ID
        self._title_to_id_map[str(ID)] = title

    @abc.abstractmethod
    def _read_native_playlist_tracks(self, native_playlist: Playlist | NativePlaylist) -> List[AudioTag]:
        """Read tracks from a native playlist. Must be implemented by subclasses."""

    @abc.abstractmethod
    def _get_native_playlist_track_count(self, native_playlist: Playlist | NativePlaylist) -> int:
        """Get the total number of tracks in a native playlist. Must be implemented by subclasses."""

    @abc.abstractmethod
    def _read_track_metadata(self, track: NativeTrack) -> AudioTag:
        """Reads the metadata of a track."""

    @abc.abstractmethod
    def _create_playlist(self, title: str, tracks: List[AudioTag]) -> Playlist | None:
        """Create a native playlist in the player"""

    @abc.abstractmethod
    def _search_playlists(self, key: str, value: str | int | None = None, return_native: bool = False) -> List[Playlist]:
        """Search for playlists by key: 'all', 'title', or 'id'"""

    @abc.abstractmethod
    def _search_tracks(self, key: str, value: str | bool, return_native: bool = False) -> List[AudioTag | NativeTrack]:
        """Search for tracks in the native player format"""

    @abc.abstractmethod
    def _add_track_to_playlist(self, playlist: Playlist | NativePlaylist, track: AudioTag) -> None:
        """Add a track to a native playlist"""

    @abc.abstractmethod
    def _remove_track_from_playlist(self, playlist: Playlist | NativePlaylist, track: AudioTag) -> None:
        """Remove a track from a native playlist"""


class MediaMonkey(MediaPlayer):
    abbr = "MM"
    rating_scale = RatingScale.ZERO_TO_HUNDRED

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger("PlexSync.MediaMonkey")
        self.sdb = None
        self._title_to_id_map = {}

    @staticmethod
    def name() -> str:
        return "MediaMonkey"

    def connect(self, *args) -> None:
        import win32com.client

        try:
            self.sdb = win32com.client.Dispatch("SongsDB.SDBApplication")
            self.sdb.ShutdownAfterDisconnect = False
            self.logger.info("Successfully connected to MediaMonkey")
        except Exception as e:
            self.logger.error(f"Failed to connect to MediaMonkey: {e!s}")
            raise

    def _create_playlist(self, title: str, tracks: List[AudioTag]) -> MediaMonkeyPlaylist | None:
        if not title or not tracks:
            self.logger.warning("Title or tracks are empty for playlist creation")
            raise ValueError("Title and tracks cannot be empty.")
        nested_playlists = title.split(".")

        current_path = []
        current_playlist = self.sdb.PlaylistByTitle("")  # root

        for part in nested_playlists:
            current_path.append(part)
            path_title = ".".join(current_path)
            existing = self.search_playlists("title", path_title, return_native=True)

            if existing:
                current_playlist = existing[0]
            else:
                current_playlist = current_playlist.CreateChildPlaylist(part)

        bar = self.status_mgr.start_phase(f"Adding tracks to playlist {title}", total=len(tracks))
        for track in tracks:
            try:
                song = self.sdb.Database.QuerySongs(f"ID={track.ID}").Item
                if song:
                    current_playlist.AddTrack(song)
                else:
                    self.logger.warning(f"Track with ID {track.ID} not found in MediaMonkey database")
            except Exception as e:
                self.logger.error(f"Failed to add track ID {track.ID} to playlist: {e}")
            bar.update()
        bar.close()

        return current_playlist

    def _get_playlists(self) -> List[MediaMonkeyPlaylist]:
        return self.search_playlists("all")

    def load_playlist_tracks(self, playlist: Playlist) -> None:
        """Read tracks from a native playlist"""
        native_playlists = self.search_playlists("title", playlist.name, return_native=True)
        if not native_playlists:
            self.logger.warning(f"Playlist '{playlist.name}' not found")
            return
        native_playlist = native_playlists[0]
        if not playlist.is_auto_playlist:
            bar = None
            for j in range(native_playlist.Tracks.Count):
                if not bar and native_playlist.Tracks.Count > 100:
                    bar = self.status_mgr.start_phase(f"Reading tracks from playlist {playlist.name}", total=native_playlist.Tracks.Count)
                playlist.tracks.append(track := self._read_track_metadata(native_playlist.Tracks[j]))
                if bar:
                    bar.update()
                    self.logger.debug(f"Reading track {track} from playlist {playlist.name}")
            bar.close() if bar else None

    def _convert_playlist(self, native_playlist: MediaMonkeyPlaylist, title: str) -> Playlist | None:
        """Convert a MediaMonkey native playlist to a standard Playlist object. Also register the title → ID mapping."""
        if not native_playlist or not title:
            self.logger.warning("Invalid playlist conversion request")
            return None

        title = self._title_to_id_map.get(str(native_playlist.ID), title)
        playlist = Playlist(ID=native_playlist.ID, name=title)
        playlist.is_auto_playlist = native_playlist.isAutoplaylist

        return playlist

    def _collect_playlists(self, parent: MediaMonkeyPlaylist, prefix: str = "") -> List[Tuple[MediaMonkeyPlaylist, str]]:
        """Recursively collect playlists and their titles."""
        results = []
        for i in range(len(parent.ChildPlaylists)):
            pl = parent.ChildPlaylists[i]
            title = f"{prefix}.{pl.Title}" if prefix else pl.Title
            self._register_playlist_title(title, pl.ID)
            results.append((pl, title))
            if len(pl.ChildPlaylists):
                results.extend(self._collect_playlists(pl, title))
        return results

    def _search_playlists(self, key: str, value: str | int | None = None, return_native: bool = False) -> List[Playlist | MediaMonkeyPlaylist]:
        """Search for playlists by 'all', 'title', or 'id' using COM interface."""
        native_results = []

        if key == "all":
            native_results = self._collect_playlists(self.sdb.PlaylistByTitle(""))

        elif key == "title":
            if not self._title_to_id_map:
                self._search_playlists("all")
            playlist_id = self._title_to_id_map.get(value.lower())
            if playlist_id:
                return self._search_playlists("id", playlist_id, return_native)

        elif key == "id":  # pragma: no branch
            try:
                pl = self.sdb.PlaylistByID(int(value))
                native_results.append((pl, pl.Title))
            except Exception:
                self.logger.warning(f"Failed to retrieve playlist by ID: {value}")
                return []

        if return_native:
            return [pl for pl, _ in native_results]
        else:
            return [self._convert_playlist(pl, title) for pl, title in native_results]

    def _read_track_metadata(self, track: MediaMonkeyTrack) -> AudioTag:
        cached = self.cache_mgr.get_metadata(self.name(), track.ID)
        if cached is not None:
            return cached

        tag = AudioTag(
            artist=track.Artist.Name,
            album=track.Album.Name,
            title=track.Title,
            file_path=track.Path,
            rating=Rating.try_create(track.Rating, scale=self.rating_scale) or Rating.unrated(),
            ID=track.ID,
            track=track.TrackOrder,
            duration=int(track.SongLength / 1000) if track.SongLength else -1,
        )
        self.cache_mgr.set_metadata(self.name(), tag.ID, tag)

        return tag

    def _search_tracks(self, key: str, value: str | bool, return_native: bool = False) -> List[MediaMonkeyTrack]:
        if key == "title":
            title = value.replace('"', r'""')
            query = f'SongTitle = "{title}"'
        elif key == "rating":
            if value is True:
                value = "> 0"
            query = f"Rating {value}"
        elif key == "query":
            query = value
        elif key == "id":
            if not return_native and (cached := self.cache_mgr.get_metadata(self.name(), str(value))):
                return [cached]
            query = f"ID = {value}"
        else:
            raise KeyError(f"Invalid search mode {key}.")

        it = self.sdb.Database.QuerySongs(query)

        results = []
        counter = 0
        bar = None
        while not it.EOF:
            if return_native:
                results.append(it.Item)
            else:
                results.append(self._read_track_metadata(it.Item))
            it.Next()
            counter += 1
            if counter >= 50 and not bar:
                bar = self.status_mgr.start_phase(f"Collecting tracks from {self.name()}", initial=counter, total=None)
            bar.update() if bar else None
        bar.close() if bar else None
        return results

    def _update_rating(self, track: AudioTag, rating: Rating) -> None:
        self.logger.debug(f"Updating rating for {track} to {rating.to_display()}")
        try:
            song = self.search_tracks("id", track.ID, return_native=True)[0]
            song.Rating = rating.to_float(self.rating_scale)
            song.UpdateDB()
        except Exception as e:
            self.logger.error(f"Failed to update rating: {e!s}")
            raise

    def _add_track_to_playlist(self, playlist: Playlist | MediaMonkeyPlaylist, track: AudioTag) -> None:
        """Add a track to a playlist using the Playlist object"""
        results = self.search_playlists("id", playlist.ID, return_native=True)
        if not results:
            self.logger.warning(f"Native playlist not found for {playlist.name}")
            return
        native_playlist = results[0]

        matches = self.search_tracks("id", track.ID, return_native=True)
        if not matches:
            self.logger.warning(f"Could not find track for: {track} in {self.name()}")
            return
        native_playlist.AddTrack(matches[0])

    def _remove_track_from_playlist(self, playlist: Playlist | MediaMonkeyPlaylist, track: AudioTag) -> None:
        """Remove a track from a playlist using the Playlist object"""
        results = self.search_playlists("id", playlist.ID, return_native=True)
        if not results:
            self.logger.warning(f"Native playlist not found for {playlist.name}")
            return
        native_playlist = results[0]

        matches = self.search_tracks("id", track.ID, return_native=True)
        if not matches:
            self.logger.warning(f"Could not find track for: {track} in {self.name()}")
            return
        native_playlist.RemoveTrack(matches[0])

    def _read_native_playlist_tracks(self, native_playlist: MediaMonkeyPlaylist) -> List[AudioTag]:
        tracks = []
        for j in range(native_playlist.Tracks.Count):
            tracks.append(self._read_track_metadata(native_playlist.Tracks[j]))
        return tracks

    def _get_native_playlist_track_count(self, native_playlist: MediaMonkeyPlaylist) -> int:
        return native_playlist.Tracks.Count


class Plex(MediaPlayer):
    abbr = "PP"
    rating_scale = RatingScale.ZERO_TO_TEN
    maximum_connection_attempts = 3
    album_empty_alias = "[Unknown Album]"

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger("PlexSync.PlexPlayer")
        self.account = None
        self.plex_api_connection = None
        self.music_library = None

    @staticmethod
    def name() -> str:
        return "Plex"

    def connect(self) -> None:
        server = self.config_mgr.server
        username = self.config_mgr.username
        password = self.config_mgr.passwd
        token = self.config_mgr.token
        if not token and not password:
            self.logger.error("Plex token or password is required for Plex player")
            raise ValueError("Plex token or password is required for Plex player")
        if not server or not username:
            self.logger.error("Plex server and username are required for Plex player")
            raise ValueError("Plex server and username are required for Plex player")

        self.logger.info(f"Connecting to Plex server {server} as {username}")
        self.account = self._authenticate(server, username, password, token)

        self.logger.info(f"Connecting to remote player {self.name()} on the server {server}")
        try:
            self.plex_api_connection = self.account.resource(server).connect(timeout=5)
            self.logger.info(f"Successfully connected to {server}")
        except NotFound as e:
            self.logger.error(f"Failed to connect to {self.name()}")
            raise SystemExit(1) from e

        self.logger.info("Looking for music libraries")
        music_libraries = {section.key: section for section in self.plex_api_connection.library.sections() if section.type == "artist"}

        if len(music_libraries) == 0:
            self.logger.error("No music library found")
            raise SystemExit(1)
        elif len(music_libraries) == 1:
            self.music_library = next(iter(music_libraries.values()))
            self.logger.debug("Found 1 music library")
        else:
            print("Found multiple music libraries:")
            for key, library in music_libraries.items():
                print(f"\t[{key}]: {library.title}")
            while True:
                try:
                    choice = input("Select the library to sync with: ")
                    self.music_library = music_libraries[int(choice)]
                    break
                except (ValueError, KeyError):
                    print("Invalid choice. Please enter a valid number corresponding to the library.")

    def _authenticate(self, server: str, username: str, password: str, token: str) -> MyPlexAccount:
        connection_attempts_left = self.maximum_connection_attempts
        while connection_attempts_left > 0:
            time.sleep(0.5)  # important. Otherwise, the above print statement can be flushed after
            if not password and not token:
                password = getpass.getpass()
            try:
                if password:
                    return MyPlexAccount(username=username, password=password)
                elif token:  # pragma: no branch
                    return MyPlexAccount(username=username, token=token)
            except NotFound:
                print(f"Username {username}, password or token wrong for server {server}.")
                password = ""
                connection_attempts_left -= 1
            except BadRequest as error:
                self.logger.warning(f"Failed to connect: {error}")
                connection_attempts_left -= 1

        self.logger.error(f"Exiting after {self.maximum_connection_attempts} failed attempts ...")
        raise SystemExit(1)

    # --- Playlist Methods ---
    def _collect_playlists(self) -> List[Tuple[PlexPlaylist, str]]:
        results = []
        for playlist in self.plex_api_connection.playlists():
            if playlist.playlistType != "audio":
                continue
            title = playlist.title
            self._register_playlist_title(title, playlist.key)
            results.append((playlist, title))
        return results

    def _search_playlists(self, key: str, value: str | int, return_native: bool = False) -> List[Playlist | PlexPlaylist]:
        native_results = []

        if key == "all":
            native_results = self._collect_playlists()

        elif key == "title":
            if not self._title_to_id_map:
                self._search_playlists("all", True)

            playlist_id = self._title_to_id_map.get(value.lower())
            if playlist_id:
                return self._search_playlists("id", playlist_id, return_native)

        elif key == "id":  # pragma: no branch
            try:
                pl = self.music_library.fetchItem(value)
                native_results.append((pl, pl.title))
            except Exception as e:
                self.logger.warning(f"Failed to retrieve playlist by ID '{value}': {e}")
                return []
        if return_native:
            return [pl for pl, _ in native_results]
        else:
            return [self._convert_playlist(pl) for pl, title in native_results]

    def _create_playlist(self, title: str, tracks: List[AudioTag]) -> PlexPlaylist | None:
        if not tracks:
            return None
        plex_tracks = []
        for track in tracks:
            try:
                matches = self.search_tracks("id", track.ID, return_native=True)
                plex_tracks.append(matches[0])
            except Exception as e:
                self.logger.error(f"Failed to search for track {track.ID}: {e!s}")

        if plex_tracks:
            return self.plex_api_connection.createPlaylist(title=title, items=plex_tracks)
        self.logger.warning("No tracks found to create playlist")
        return None

    def load_playlist_tracks(self, playlist: Playlist) -> None:
        """Read tracks from a native playlist"""
        results = self.search_playlists("title", playlist.name, return_native=True)
        if not results:  # Check if list is empty
            self.logger.warning(f"Native playlist not found for {playlist.name}")
            return
        native_playlist = results[0]

        bar = None
        if not playlist.is_auto_playlist:
            for item in native_playlist.items():
                if not bar and len(native_playlist.items()) > 100:
                    bar = self.status_mgr.start_phase(f"Reading tracks from playlist {playlist.name}", total=len(native_playlist.items()))
                playlist.tracks.append(self._read_track_metadata(item))
                bar.update() if bar else None
                self.logger.debug(f"Reading track {item} from playlist {playlist.name}") if bar else None
            bar.close() if bar else None

    def _convert_playlist(self, native_playlist: PlexPlaylist) -> Playlist | None:
        ID = native_playlist.key.split("/")[-1] if "/" in native_playlist.key else native_playlist.key
        title = self._title_to_id_map.get(str(ID), native_playlist.title)
        playlist = Playlist(ID=ID, name=title)
        playlist.is_auto_playlist = native_playlist.smart
        return playlist

    def _get_playlists(self) -> List[PlexPlaylist]:
        """Read all playlists from the Plex server and convert them to internal Playlist objects."""
        self.logger.info(f"Reading playlists from the {self.name()} player")
        return self.search_playlists("all")

    def _add_track_to_playlist(self, playlist: Playlist | PlexPlaylist, track: AudioTag) -> None:
        """Add a track to a playlist using the Playlist object"""
        results = self._search_playlists("id", playlist.ID, return_native=True)

        if not results:
            self.logger.warning(f"Native playlist not found for {playlist.name}")
            return
        native_playlist = results[0]

        try:
            matches = self.search_tracks("id", track.ID, return_native=True)
            native_playlist.addItems(matches[0])
        except Exception as e:
            self.logger.error(f"Failed to search for track {track.ID}: {e!s}")
            raise

    def _remove_track_from_playlist(self, playlist: Playlist | PlexPlaylist, track: AudioTag) -> None:
        """Remove a track from a playlist using the Playlist object"""
        results = self._search_playlists("id", playlist.ID, return_native=True)

        if not results:
            self.logger.warning(f"Native playlist not found for {playlist.name}")
            return
        native_playlist = results[0]

        try:
            matches = self.search_tracks("id", track.ID, return_native=True)
            native_playlist.removeItem(matches[0])
        except Exception as e:
            self.logger.error(f"Failed to search for track {track.ID}: {e!s}")
            raise

    def _read_native_playlist_tracks(self, native_playlist: PlexPlaylist) -> List[AudioTag]:
        return [self._read_track_metadata(item) for item in native_playlist.items()]

    def _get_native_playlist_track_count(self, native_playlist: PlexPlaylist) -> int:
        return len(native_playlist.items())

    # --- Track Methods ---
    def _read_track_metadata(self, track: plexapi.audio.Track) -> AudioTag:
        return AudioTag(
            artist=track.grandparentTitle,
            album=track.parentTitle,
            title=track.title,
            file_path=track.locations[0],
            rating=Rating.try_create(track.userRating, scale=self.rating_scale) or Rating.unrated(),
            ID=track.key.split("/")[-1] if "/" in track.key else track.key,
            track=track.index,
            duration=int(track.duration / 1000) if track.duration else -1,  # Convert ms to seconds
        )

    def _search_tracks(self, key: str, value: str | bool, return_native: bool = False) -> List[PlexTrack]:
        if key == "title":
            matches = self.music_library.searchTracks(title=value)
        elif key == "rating":
            if value is True:
                value = "0"
            print(f"Collecting tracks from {self.name()}.  This may take some time for large libraries.")
            matches = self.music_library.searchTracks(**{"track.userRating!": value})
        elif key == "id":
            matches = [self.music_library.fetchItem(int(value))]
        else:
            raise KeyError(f"Invalid search mode {key}.")
        if return_native:
            return matches

        tracks = []
        bar = None
        if len(matches) >= 500:
            bar = self.status_mgr.start_phase(f"Reading track metadata from {self.name()}", total=len(matches))

        for match in matches:
            bar.update() if bar else None
            track = self._read_track_metadata(match)
            tracks.append(track)

        bar.close() if bar else None
        return tracks

    def _update_rating(self, track: AudioTag, rating: Rating) -> None:
        self.logger.debug(f"Updating rating on {self.name()} for {track} to {rating.to_display()}")

        try:
            song = self.search_tracks("id", track.ID, return_native=True)[0]
            song.edit(**{"userRating.value": Rating.to_float(rating, scale=self.rating_scale)})
        except Exception as e:
            self.logger.error(f"Failed to update rating on {track}: {e!s}")
            raise
        self.logger.info(f"Successfully updated rating for {track}")


class FileSystem(MediaPlayer):
    RATING_SCALE = RatingScale.NORMALIZED
    SEARCH_THRESHOLD = 75  # Fuzzy search threshold for track title matching

    def __init__(self):
        self.fsp = None
        self.logger = logging.getLogger("PlexSync.FileSystem")
        self.abbr = "FS"
        super().__init__()

    @staticmethod
    def name() -> str:
        return "FileSystem"

    def connect(self) -> None:
        """Connect to the local music library, loading only the metadata the user asked to sync."""
        # ── Phase 0: cheap directory walk ────────────────────────────────────────────
        self.fsp = FileSystemProvider()
        self.fsp.scan_media_files()

        # ── Phase 1: heavy track-tag load (only if TRACKS are being synced) ─────────
        if SyncItem.TRACKS in self.config_mgr.sync:
            tracks = self.fsp.get_tracks()
            bar = self.status_mgr.start_phase(
                f"Reading track metadata from {self.name()}",
                total=len(tracks),
            )
            for file_path in tracks:
                self._read_track_metadata(file_path)  # disk parse + cache write
                bar.update()
            bar.close()

        # ── Phase 2: light playlist-header load (only if PLAYLISTS are being synced) ─
        # TODO: playlist metadata isn't retained - this should be switched to simple registration of the playlist map
        if SyncItem.PLAYLISTS in self.config_mgr.sync:
            for pl_path in self.fsp._get_playlist_paths():
                self.fsp.read_playlist_metadata(pl_path)

        # ── Phase 3: conflict resolution + final cache refresh (runs only if needed) ─
        finalizing_tracks = self.fsp.finalize_scan()
        if finalizing_tracks:
            bar = self.status_mgr.start_phase(
                f"Finalizing track indexing from {self.name()}",
                total=len(finalizing_tracks),
            )
            for track in finalizing_tracks:
                # overwrite cache entry with conflict-free AudioTag (RAM-only)
                self.cache_mgr.set_metadata(self.name(), track.ID, track)
                bar.update()
            bar.close()

    # --- Track Methods ---
    def _read_track_metadata(self, file_path: Path | str) -> AudioTag:
        """Retrieve metadata from cache or read from file."""
        str_path = str(file_path)

        cached = self.cache_mgr.get_metadata(self.name(), str_path, force_enable=True)
        if cached:
            self.logger.trace(f"Cache hit for {file_path}")
            return cached

        tag = self.fsp.read_track_metadata(file_path)
        self.cache_mgr.set_metadata(self.name(), tag.ID, tag, force_enable=True) if tag else None
        return tag

    def _search_tracks(self, key: str, value: str | bool, return_native: bool = False) -> List[AudioTag]:
        """Search for audio files matching criteria"""
        _ = return_native

        tracks = []
        player_mask = self.cache_mgr.metadata_cache.cache["player_name"] == self.name().lower()

        if key == "id":
            tracks = [self.cache_mgr.get_metadata(self.name(), value, force_enable=True)]
        elif key == "title":
            title_mask = self.cache_mgr.metadata_cache.cache[key].apply(lambda x: fuzz.ratio(str(x).lower(), str(value).lower()) >= self.SEARCH_THRESHOLD)
            tracks = self.cache_mgr.get_tracks_by_filter(player_mask & title_mask)
        elif key == "rating":  # pragma: no branch
            if value is True:
                value = 0
            rating_mask = self.cache_mgr.metadata_cache.cache["rating"] > Rating(value, scale=RatingScale.ZERO_TO_FIVE).to_float(RatingScale.NORMALIZED)
            tracks = self.cache_mgr.get_tracks_by_filter(player_mask & rating_mask)
        return tracks

    def _update_rating(self, track: AudioTag, rating: Rating) -> None:
        self.fsp.update_track_metadata(file_path=track.ID, rating=rating)
        track.rating = rating
        self.cache_mgr.set_metadata(self.name(), track.ID, track, force_enable=True)
        self.logger.info(f"Successfully updated rating for {track}")

    # --- Playlist Methods ---
    def _create_playlist(self, title: str, tracks: List[AudioTag]) -> Playlist | None:
        """Create a new M3U playlist file"""
        if not tracks:
            return None
        playlist = self.fsp.create_playlist(title, is_extm3u=True)
        bar = None
        if playlist:
            if len(tracks) > 100:
                bar = self.status_mgr.start_phase(f"Adding tracks to playlist {title}", total=len(tracks))
            for track in tracks:
                self._add_track_to_playlist(playlist, track)
                bar.update() if bar else None
            bar.close() if bar else None
        return playlist

    def _search_playlists(self, key: str, value: str | int | None = None, return_native: bool = False) -> List[Playlist]:
        if key == "all":
            return self.fsp.get_playlists()
        elif key == "title":
            return self.fsp.get_playlists(title=value)
        elif key == "id":  # pragma: no branch
            return self.fsp.get_playlists(path=value)

    def _add_track_to_playlist(self, playlist: Playlist, track: AudioTag) -> None:
        """Add a track to a playlist"""
        if not playlist:
            self.logger.warning("Playlist not found or invalid")
            return
        self.logger.debug(f"Adding track {track.ID} to playlist {playlist.name}")
        self.fsp.add_track_to_playlist(playlist.ID, track, is_extm3u=playlist.is_extm3u)

    def _remove_track_from_playlist(self, playlist: Playlist, track: AudioTag) -> None:
        """Remove a track from a playlist"""
        raise NotImplementedError
        self.fsp.remove_track_from_playlist(playlist, track)

    def _read_native_playlist_tracks(self, native_playlist: Playlist) -> List[AudioTag]:
        return self.fsp.get_tracks_from_playlist(native_playlist.ID)

    def _get_native_playlist_track_count(self, native_playlist: Playlist) -> int:
        if len(native_playlist.tracks) == 0:
            self.fsp.get_tracks_from_playlist(native_playlist.ID)
        return len(native_playlist.tracks)
