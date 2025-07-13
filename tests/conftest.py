import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ratings import Rating, RatingScale
from sync_items import AudioTag, Playlist


# TODO: remove extraneous track and playlist factories in teh rest of hte source code
@pytest.fixture
def track_factory():
    def _factory(ID="1", title="Title", artist="Artist", album="Album", track=1, rating=1.0):
        rating = Rating(rating, scale=RatingScale.NORMALIZED)
        return AudioTag(ID=ID, title=title, artist=artist, album=album, track=track, rating=rating)

    return _factory


@pytest.fixture
def playlist_factory(track_factory):
    def _factory(ID="pl1", name="My Playlist", tracks=None, is_auto_playlist=False):
        playlist = Playlist(ID=ID, name=name)
        playlist.tracks = tracks or [track_factory(ID="t1"), track_factory(ID="t2")]
        playlist.is_auto_playlist = is_auto_playlist
        return playlist

    return _factory


@pytest.fixture(scope="session")
def patch_paths(tmp_path_factory):
    logs_path = tmp_path_factory.mktemp("logs")
    cache_path = tmp_path_factory.mktemp("cache")
    return logs_path, cache_path


@pytest.fixture(scope="session")
def config_args():
    return ["test_runner.py", "--source", "plex", "--destination", "mediamonkey", "--sync", "tracks"]


@pytest.fixture(scope="function", autouse=True)
def initialize_manager(monkeypatch, patch_paths, config_args):
    sys.argv = config_args

    logs_path, cache_path = patch_paths

    monkeypatch.setattr("manager.log_manager.LogManager.LOG_DIR", str(logs_path))
    monkeypatch.setattr("manager.cache_manager.CacheManager.MATCH_CACHE_FILE", str(cache_path / "matches.pkl"))
    monkeypatch.setattr("manager.cache_manager.CacheManager.METADATA_CACHE_FILE", str(cache_path / "metadata.pkl"))
    monkeypatch.setattr("manager.config_manager.ConfigManager.CONFIG_FILE", "./does_not_exist.ini")
    from manager import get_manager

    # Avoid re-initialization
    mgr = get_manager()
    monkeypatch.setattr(mgr, "get_stats_manager", lambda: MagicMock())
    monkeypatch.setattr(mgr, "get_status_manager", lambda: MagicMock())
    if not getattr(mgr, "_initialized", False):
        mgr.initialize()


"""
Shared Plex API fixtures for testing.

This module provides comprehensive Plex API mocking capabilities for both unit tests
and end-to-end integration tests. The fixtures simulate realistic Plex server behavior
including search, fetchItem, playlist operations, and error conditions.
"""


def _find_track_by_id(test_tracks, value):
    """Return a track matching the given ID."""
    for track in test_tracks:
        track_id = getattr(track, "id", None)
        if track_id == value:
            return track
    raise RuntimeError("search error")


def _find_playlist_by_id(test_playlists, value):
    """Return a playlist matching the given ID."""
    for playlist in test_playlists:
        playlist_id = getattr(playlist, "key", None)
        if playlist_id == str(value) or playlist_id == value:
            return playlist
    raise RuntimeError("search error")


def _mock_search_tracks(api, *args, **kwargs):
    """Mock implementation of searchTracks for the plex_api fixture."""
    test_tracks = getattr(api, "test_tracks", [])
    if not args and not kwargs:
        return test_tracks
    if "title" in kwargs:
        title = kwargs["title"]
        return [t for t in test_tracks if getattr(t, "title", None) == title]
    if "track.userRating!" in kwargs:
        # Simulate rating search (very basic)
        return test_tracks
    if args:
        try:
            return [_find_track_by_id(test_tracks, args[0])]
        except RuntimeError:
            # searchTracks should return empty list when track not found
            return []
    return test_tracks


def _mock_fetch_item_side_effect(api, value):
    """Mock implementation of fetchItem for the plex_api fixture."""
    # Check for fetchItem_raise exception first (fixture API integration)
    exc = getattr(api, "fetchItem_raise", None)
    if exc is not None:
        raise exc

    # ID Convention:
    # - Tracks: 1-999 (start at 1)
    # - Playlists: 1000+ (start at 1000)

    value = int(value)
    if value < 1000:  # Track ID range
        test_tracks = getattr(api, "test_tracks", [])
        return _find_track_by_id(test_tracks, value)
    else:  # Playlist ID range (1000+)
        test_playlists = getattr(api, "test_playlists", [])
        return _find_playlist_by_id(test_playlists, value)


def _make_default_libraries(api, count: int = 1):
    """Return a list of MagicMock sections shaped like Plex artist libraries, with fetchItem and createPlaylist side effects."""
    libraries = []
    for i in range(count):
        lib = MagicMock(
            key=i + 1,
            type="artist",
            title=f"Music Library {i + 1}",
        )
        lib.fetchItem.side_effect = lambda v: _mock_fetch_item_side_effect(api, v)
        lib.createPlaylist.side_effect = _make_side_effect(api, "createPlaylist")
        lib.searchTracks = MagicMock(side_effect=lambda *args, **kwargs: _mock_search_tracks(api, *args, **kwargs))
        libraries.append(lib)
    return libraries


def _make_side_effect(api, name):
    """Create side effect function for various API methods."""

    def _side_effect(*args, **kwargs):
        exc = getattr(api, f"{name}_raise", None)
        if exc is not None:
            raise exc
        if name == "playlists":
            return getattr(api, "test_playlists", [])
        if hasattr(api, f"{name}_return"):
            return getattr(api, f"{name}_return")
        return None

    return _side_effect


@pytest.fixture
def plex_api():
    """
    Comprehensive Plex API mock for testing.

    Provides realistic simulation of Plex server behavior including:
    - Search operations (searchTracks with various query types)
    - Item fetching (fetchItem with ID routing)
    - Playlist operations (playlists, createPlaylist)
    - Library management (sections, library selection)
    - Error injection capabilities for testing failure scenarios

    ID Conventions:
    - Tracks: 1-999 (start at 1)
    - Playlists: 1000+ (start at 1000)

    Usage:
        api.test_tracks = [track1, track2, ...]     # Set tracks for search/fetch
        api.test_playlists = [playlist1, ...]       # Set playlists for fetch
        api.fetchItem_raise = SomeException()       # Inject fetch errors
        api.searchTracks_raise = SomeException()    # Inject search errors
        api.library_count = 2                       # Simulate multiple libraries
    """
    api = MagicMock()
    api.libraries = None
    api.library_count = 1

    resource_mock = MagicMock(name="resource")
    connection_mock = MagicMock(name="connection")

    # Create a server resource mock that has the connect method
    server_resource_mock = MagicMock(name="server_resource")

    def _connect_side_effect(*args, **kwargs):
        exc = getattr(api, "connect_raise", None)
        if exc is not None:
            raise exc
        return connection_mock

    server_resource_mock.connect = MagicMock(side_effect=_connect_side_effect)
    resource_mock.side_effect = lambda server: server_resource_mock
    api.resource = resource_mock

    def _sections_side_effect():
        if api.libraries:
            return api.libraries
        return _make_default_libraries(api, api.library_count)

    connection_mock.library = MagicMock(name="library")
    connection_mock.library.sections = MagicMock(side_effect=_sections_side_effect)
    connection_mock.playlists = MagicMock(side_effect=_make_side_effect(api, "playlists"))

    # default: no error injection
    api.account_raise = api.connect_raise = api.fetchItem_raise = api.playlists_raise = api.createPlaylist_raise = api.searchTracks_raise = None
    return api


@pytest.fixture
def plex_player(monkeypatch, request, plex_api):
    """
    Refactored Plex player fixture using the new plex_api mock and native object factories.
    """
    # Build a MagicMock manager with all required attributes
    fake_manager = MagicMock()
    config_defaults = {
        "server": "mock_server",
        "username": "mock_user",
        "passwd": "mock_password",
        "token": None,
    }
    # Allow test-time overrides
    config_overrides = getattr(request, "param", {})
    config = MagicMock(**{**config_defaults, **config_overrides})
    fake_manager.get_config_manager.return_value = config

    def myplexaccount_patch(*args, **kwargs):
        # retry/exit tests use this hook
        if getattr(plex_api, "account_raise", None):
            raise plex_api.account_raise
        return plex_api

    monkeypatch.setattr("manager.get_manager", lambda: fake_manager)
    monkeypatch.setattr("MediaPlayer.MyPlexAccount", myplexaccount_patch)
    monkeypatch.setattr("MediaPlayer.getpass.getpass", lambda *args, **kwargs: "dummy_password")

    from MediaPlayer import Plex  # Import after patching

    plex = Plex()
    plex.config_mgr = config
    plex.logger = MagicMock()
    plex.status_mgr = MagicMock()  # Add missing status_mgr mock

    for k, v in config_overrides.items():
        setattr(plex.config_mgr, k, v)

    # Assign correct mocks for account, plex_api_connection, and music_library
    plex.account = plex_api
    plex.plex_api_connection = plex_api.resource(config["server"]).connect()
    plex.music_library = plex.plex_api_connection.library.sections()[0]

    return plex


@pytest.fixture
def plex_playlist_factory():
    """Factory for native playlist MagicMock objects with Plex shape."""

    def _factory(playlist):
        mock = MagicMock()
        mock.key = str(getattr(playlist, "ID", getattr(playlist, "key", "")))
        mock.title = getattr(playlist, "name", getattr(playlist, "title", ""))
        mock.smart = getattr(playlist, "is_auto_playlist", getattr(playlist, "smart", False))
        mock.playlistType = "audio"  # Required for _collect_playlists filter
        # items() returns tracks if set, else []
        tracks = getattr(playlist, "Tracks", getattr(playlist, "tracks", []))
        if not tracks and hasattr(playlist, "tracks"):
            tracks = playlist.tracks
        mock.items.return_value = tracks if tracks is not None else []
        return mock

    return _factory


@pytest.fixture
def plex_track_factory():
    """Factory for native track MagicMock objects with Plex shape and correct key format."""

    def _factory(track):
        mock = MagicMock()
        mock.grandparentTitle = getattr(track, "artist", getattr(track, "grandparentTitle", ""))
        mock.parentTitle = getattr(track, "album", getattr(track, "parentTitle", ""))
        mock.title = getattr(track, "title", "")
        file_path = getattr(track, "file_path", None)
        mock.locations = [file_path] if file_path else getattr(track, "locations", ["/dev/null"])

        # Convert Rating object to float for Plex ZERO_TO_TEN scale
        rating = getattr(track, "rating", getattr(track, "userRating", None))
        if hasattr(rating, "to_float"):
            mock.userRating = rating.to_float()
        else:
            mock.userRating = float(rating) if rating is not None else None

        key_val = getattr(track, "ID", getattr(track, "key", getattr(track, "title", 1)))
        mock.id = key_val
        mock.key = str(key_val)

        return mock

    return _factory


"""
Shared MediaMonkey API fixtures for testing.

This module provides comprehensive MediaMonkey COM interface mocking for both unit tests
and integration tests. The fixtures simulate realistic MediaMonkey behavior including:

- SQL query processing with regex-based pattern matching
- Track and playlist creation with MediaMonkey-shaped objects
- Database operations (QuerySongs, PlaylistByTitle, PlaylistByID)
- COM object interaction patterns (SimpleNamespace with proper attributes)
- Error injection capabilities for testing failure scenarios
- Nested playlist hierarchies and track collections

Key fixtures:
- mm_track_factory: Creates MediaMonkey track objects with Artist/Album nested attributes
- mm_playlist_factory: Creates playlists with TracksCollection and AddTrack/RemoveTrack spies
- mm_api: Complete SDBMock with query processing and data injection methods
- mm_player: Full MediaMonkey player with patched win32com.client.Dispatch
"""


class SQLQueryProcessor:
    """Simulates MediaMonkey SQL query processing with regex patterns."""

    def process_query(self, sql_query, tracks):
        if "SongTitle" in sql_query:
            m = re.search(r'SongTitle = "(.+)"$', sql_query)
            if m:
                title = m.group(1).replace('""', '"')
                return [t for t in tracks if getattr(t, "Title", None) == title]

        if "ID" in sql_query and "=" in sql_query:
            m = re.search(r"ID\s*=\s*(\d+)", sql_query)
            if m:
                tid = int(m.group(1))
                return [t for t in tracks if getattr(t, "ID", None) == tid]

        if "Rating" in sql_query:
            rating_patterns = [
                (r"Rating\s*>\s*(\d+(?:\.\d+)?)", lambda track_rating, value: track_rating > float(value)),
                (r"Rating\s*>=\s*(\d+(?:\.\d+)?)", lambda track_rating, value: track_rating >= float(value)),
                (r"Rating\s*=\s*(\d+(?:\.\d+)?)", lambda track_rating, value: track_rating == float(value)),
                (r"Rating\s*<=\s*(\d+(?:\.\d+)?)", lambda track_rating, value: track_rating <= float(value)),
                (r"Rating\s*<\s*(\d+(?:\.\d+)?)", lambda track_rating, value: track_rating < float(value)),
                (r"Rating\s*!=\s*(\d+(?:\.\d+)?)", lambda track_rating, value: track_rating != float(value)),
            ]

            for pattern, comparator in rating_patterns:
                m = re.search(pattern, sql_query)
                if m:
                    threshold = m.group(1)
                    return [t for t in tracks if hasattr(t, "Rating") and comparator(getattr(t, "Rating", 0), threshold)]

        return []


@pytest.fixture
def mm_track_factory():
    def _factory(ID=1, Title="Track", Rating=0, ArtistName="Artist", AlbumName="Album", Path="/mock/path.mp3", TrackOrder=1, SongLength=180000):
        track = SimpleNamespace()
        track.ID = ID
        track.Title = Title
        track.Rating = Rating
        track.Artist = SimpleNamespace(Name=ArtistName)
        track.Album = SimpleNamespace(Name=AlbumName)
        track.Path = Path
        track.TrackOrder = TrackOrder
        track.SongLength = SongLength
        return track

    return _factory


@pytest.fixture
def mm_playlist_factory():
    def _factory(ID=1, Title="Playlist", isAutoplaylist=False, tracks=None, children=None):
        class TracksCollection:
            """Simulates MediaMonkey playlist track collection."""

            def __init__(self, tracks_list=None):
                self._tracks = tracks_list or []

            @property
            def Count(self):
                return len(self._tracks)

            def __getitem__(self, index):
                return self._tracks[index]

            def __len__(self):
                return len(self._tracks)

            def append(self, track):
                self._tracks.append(track)

            def remove(self, track):
                self._tracks.remove(track)

        playlist = SimpleNamespace()
        playlist.ID = ID
        playlist.Title = Title
        playlist.isAutoplaylist = isAutoplaylist
        playlist.Tracks = TracksCollection(tracks)
        playlist.ChildPlaylists = children or []

        playlist.AddTrack_calls = []
        playlist.RemoveTrack_calls = []

        def add_track_spy(track):
            playlist.AddTrack_calls.append(track)
            playlist.Tracks.append(track)

        def remove_track_spy(track):
            playlist.RemoveTrack_calls.append(track)
            playlist.Tracks.remove(track)

        playlist.AddTrack = add_track_spy
        playlist.RemoveTrack = remove_track_spy
        playlist.CreateChildPlaylist = lambda title: _factory(ID=ID * 10 + len(playlist.ChildPlaylists) + 1, Title=title)
        return playlist

    return _factory


class QueryResultIterator:
    """Simulates MediaMonkey query result iteration with EOF handling."""

    def __init__(self, items):
        self._items = list(items)
        self._index = 0
        self.EOF = len(self._items) == 0
        self.Item = self._items[self._index] if self._items else None

    def Next(self):
        if self._index + 1 < len(self._items):
            self._index += 1
            self.Item = self._items[self._index]
            self.EOF = False
        else:
            self._index = len(self._items)
            self.Item = None
            self.EOF = True


class DatabaseMock:
    def __init__(self, sdb):
        self._sdb = sdb
        self.QuerySongs = MagicMock(side_effect=self._query_songs)

    def _query_songs(self, sql_query):
        if self._sdb.QuerySongs_raise:
            raise self._sdb.QuerySongs_raise
        if self._sdb.QuerySongs_return is not None:
            return self._sdb.QuerySongs_return
        return QueryResultIterator(SQLQueryProcessor().process_query(sql_query, self._sdb._tracks))


class TracksCollection:
    def __init__(self, tracks_list=None):
        self._tracks = tracks_list or []

    @property
    def Count(self):
        return len(self._tracks)

    def __getitem__(self, index):
        return self._tracks[index]

    def __len__(self):
        return len(self._tracks)

    def append(self, track):
        self._tracks.append(track)

    def remove(self, track):
        self._tracks.remove(track)


class SDBMock:
    """Complex MediaMonkey database state simulation with multiple responsibilities."""

    def __init__(self):
        self.QuerySongs_raise = None
        self.QuerySongs_return = None
        self._tracks = []
        self._playlists = []
        self.Database = DatabaseMock(self)
        self._playlist_by_id = {}
        self._playlist_by_title = {}
        self._root_playlist = self._make_playlist(ID=0, Title="", children=[])
        self.PlaylistByTitle = MagicMock(side_effect=self._playlist_by_title_impl)
        self.PlaylistByID = MagicMock(side_effect=self._playlist_by_id_impl)

    def set_tracks(self, tracks):
        self._tracks = tracks

    def set_playlists(self, playlists):
        self._playlists = playlists
        self._playlist_by_id = {pl.ID: pl for pl in playlists}
        self._playlist_by_title = {pl.Title.lower(): pl for pl in playlists}
        self._root_playlist.ChildPlaylists = playlists

    def _playlist_by_title_impl(self, title):
        if title == "":
            return self._root_playlist
        return self._playlist_by_title.get(title.lower())

    def _playlist_by_id_impl(self, id):
        return self._playlist_by_id.get(int(id))

    def _make_playlist(self, ID=1, Title="Playlist", isAutoplaylist=False, tracks=None, children=None):
        pl = SimpleNamespace()
        pl.ID = ID
        pl.Title = Title
        pl.isAutoplaylist = isAutoplaylist
        pl.Tracks = TracksCollection(tracks)
        pl.ChildPlaylists = children or []
        pl.AddTrack = lambda track: pl.Tracks.append(track)
        pl.RemoveTrack = lambda track: pl.Tracks.remove(track)
        pl.CreateChildPlaylist = lambda title: self._make_playlist(ID=ID * 10 + len(pl.ChildPlaylists) + 1, Title=title)
        return pl


@pytest.fixture
def mm_api():
    """MediaMonkey API mock with side effect configuration.

    Setup error injection: mm_api.connect_raise = Exception("msg")
    Setup query errors: mm_api.QuerySongs_raise = Exception("msg")
    Override query results: mm_api.QuerySongs_return = mock_iterator

    Inject tracks: mm_api.set_tracks([mm_track_factory(ID=1, Title="Song")])
    Inject playlists: mm_api.set_playlists([mm_playlist_factory(ID=100, Title="Playlist")])
    Inject tracks into playlists: mm_playlist_factory(tracks=[track1, track2])
    """
    sdb = SDBMock()
    sdb.connect_raise = None
    sdb.set_tracks([])
    sdb.set_playlists([])
    return sdb


@pytest.fixture
def mm_player(monkeypatch, request, mm_api):
    def dispatch_side_effect(*args, **kwargs):
        exc = getattr(mm_api, "connect_raise", None)
        if exc is not None:
            raise exc
        return mm_api

    monkeypatch.setattr("win32com.client.Dispatch", dispatch_side_effect)

    fake_manager = MagicMock()
    config_defaults = {
        "server": "mock_server",
        "username": "mock_user",
        "passwd": "mock_password",
        "token": None,
    }
    config_overrides = getattr(request, "param", {}).get("config", {})
    config = MagicMock(**{**config_defaults, **config_overrides})
    fake_manager.get_config_manager.return_value = config
    fake_manager.get_cache_manager.return_value = MagicMock()
    fake_manager.get_status_manager.return_value = MagicMock()
    monkeypatch.setattr("manager.get_manager", lambda: fake_manager)

    from MediaPlayer import MediaMonkey

    player = MediaMonkey()
    player.config_mgr = config
    player.logger = MagicMock()
    player.status_mgr = MagicMock()
    player.cache_mgr = MagicMock()
    player.cache_mgr.get_metadata.return_value = None
    player.cache_mgr.set_metadata.return_value = None
    player.sdb = mm_api
    for k, v in config_overrides.items():
        setattr(player.config_mgr, k, v)
    return player
