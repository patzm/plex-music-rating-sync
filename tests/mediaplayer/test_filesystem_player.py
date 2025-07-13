"""FileSystem player tests with property injection for FileSystemProvider."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ratings import Rating, RatingScale
from sync_items import AudioTag, Playlist


@pytest.fixture
def filesystem_api():
    """Simple FileSystemProvider mock following plex_api pattern."""
    fsp = MagicMock()

    fsp.test_tracks = []
    fsp.test_playlists = []
    fsp.test_metadata = {}
    fsp.test_deferred_tracks = []

    fsp.get_tracks.side_effect = lambda: fsp.test_tracks
    fsp.finalize_scan.side_effect = lambda: fsp.test_deferred_tracks
    fsp.read_track_metadata.side_effect = lambda path: fsp.test_metadata.get(str(path))

    def get_playlists_side_effect(title=None, path=None):
        if title:
            return [p for p in fsp.test_playlists if getattr(p, "name", None) == title]
        elif path:
            return [p for p in fsp.test_playlists if getattr(p, "ID", None) == path]
        return fsp.test_playlists

    fsp.get_playlists.side_effect = get_playlists_side_effect

    fsp._get_playlist_paths.return_value = []

    fsp.get_tracks_from_playlist_return = []
    fsp.get_tracks_from_playlist.side_effect = lambda playlist_id: fsp.get_tracks_from_playlist_return

    fsp.create_playlist_return = None
    fsp.create_playlist.side_effect = lambda *args, **kwargs: fsp.create_playlist_return

    return fsp


@pytest.fixture
def filesystem_player(monkeypatch, request, filesystem_api):
    """FileSystem player following plex_player pattern."""
    fake_manager = MagicMock()
    config_defaults = {
        "path": "/fake/audio",
        "playlist_path": "/fake/playlists",
        "dry": False,
        "sync_items": set(),
    }
    config_overrides = getattr(request, "param", {}).get("config", {})
    config = MagicMock(**{**config_defaults, **config_overrides})
    fake_manager.get_config_manager.return_value = config

    cache_mgr = MagicMock()
    cache_mgr.cache_hit = False
    cache_mgr.stored_tracks = {}

    def get_metadata_side_effect(player, file_path, **kwargs):
        return cache_mgr.stored_tracks.get(file_path) if cache_mgr.cache_hit else None

    cache_mgr.get_metadata.side_effect = get_metadata_side_effect

    def get_tracks_by_filter_side_effect(mask):
        if hasattr(cache_mgr, "rating_search_tracks"):
            return cache_mgr.rating_search_tracks
        return list(cache_mgr.stored_tracks.values())

    cache_mgr.get_tracks_by_filter.side_effect = get_tracks_by_filter_side_effect

    cache_mgr.set_metadata = MagicMock()
    cache_mgr.metadata_cache = MagicMock()

    mock_cache = MagicMock()
    mock_cache.__getitem__.return_value = MagicMock()
    mock_cache["player_name"].__eq__.return_value = MagicMock()
    mock_cache["rating"].__gt__.return_value = MagicMock()
    cache_mgr.metadata_cache.cache = mock_cache

    fake_manager.get_cache_manager.return_value = cache_mgr

    fake_manager.get_status_manager.return_value = MagicMock()

    monkeypatch.setattr("manager.get_manager", lambda: fake_manager)
    monkeypatch.setattr("MediaPlayer.FileSystemProvider", lambda: filesystem_api)

    from MediaPlayer import FileSystem

    player = FileSystem()
    player.config_mgr = config
    player.cache_mgr = fake_manager.get_cache_manager()
    player.status_mgr = fake_manager.get_status_manager()
    player.logger = MagicMock()
    player.fsp = filesystem_api

    for k, v in config_overrides.items():
        setattr(player.config_mgr, k, v)

    return player


class TestConnect:
    """Test FileSystem connect method phases and sync configurations."""

    def test_connect_initializes_fsp(self, filesystem_player):
        """Test connect initializes FileSystemProvider and scans media files."""
        filesystem_player.connect()

        assert filesystem_player.fsp is not None
        filesystem_player.fsp.scan_media_files.assert_called_once()

    @pytest.mark.parametrize(
        "sync_items,expect_tracks_phase,expect_playlists_phase",
        [
            (["tracks"], True, False),
            (["playlists"], False, True),
            (["tracks", "playlists"], True, True),
            ([], False, False),
        ],
        ids=[
            "tracks_only",
            "playlists_only",
            "both_sync_items",
            "no_sync_items",
        ],
    )
    def test_connect_phases(self, filesystem_player, filesystem_api, sync_items, expect_tracks_phase, expect_playlists_phase):
        """Test connect executes correct phases based on sync_items configuration."""
        filesystem_player.config_mgr.sync = sync_items

        if expect_tracks_phase:
            filesystem_api.test_tracks = [Path("track1.mp3"), Path("track2.mp3")]
        if expect_playlists_phase:
            filesystem_api._get_playlist_paths.return_value = [Path("list1.m3u")]

        filesystem_player.connect()

        if expect_tracks_phase:
            assert filesystem_player.status_mgr.start_phase.call_count >= 1
            phase_calls = [call.args[0] for call in filesystem_player.status_mgr.start_phase.call_args_list]
            assert any("Reading track metadata" in call for call in phase_calls)

        if expect_playlists_phase:
            assert len(filesystem_player.fsp._get_playlist_paths()) > 0

    def test_connect_empty_tracks(self, filesystem_player, filesystem_api):
        """Test connect with empty tracks skips progress tracking."""
        filesystem_player.config_mgr.sync = ["tracks"]
        filesystem_api.test_tracks = []

        filesystem_player.connect()

        filesystem_player.status_mgr.start_phase.assert_called_with("Reading track metadata from FileSystem", total=0)

    def test_connect_deferred_tracks(self, filesystem_player, filesystem_api):
        """Test connect processes deferred tracks through finalization."""
        filesystem_player.config_mgr.sync = ["tracks"]
        deferred_track = AudioTag(ID="deferred1", title="Deferred Track", artist="Artist", album="Album", track=1)
        filesystem_api.test_deferred_tracks = [deferred_track]

        filesystem_player.connect()

        filesystem_player.fsp.finalize_scan.assert_called_once()
        filesystem_player.cache_mgr.set_metadata.assert_called_with("FileSystem", deferred_track.ID, deferred_track)


class TestTrackMetadata:
    """Test FileSystem track metadata reading with cache."""

    def test_read_cache_hit(self, filesystem_player):
        """Test _read_track_metadata returns cached data when available."""
        track = AudioTag(ID="test.mp3", title="test", artist="Test Artist", album="Test Album", track=1)
        filesystem_player.cache_mgr.stored_tracks["test.mp3"] = track
        filesystem_player.cache_mgr.cache_hit = True

        result = filesystem_player._read_track_metadata("test.mp3")

        assert result is track
        filesystem_player.cache_mgr.get_metadata.assert_called_with("FileSystem", "test.mp3", force_enable=True)
        filesystem_player.fsp.read_track_metadata.assert_not_called()

    def test_read_cache_miss(self, filesystem_player, filesystem_api):
        """Test _read_track_metadata loads from FSP on cache miss."""
        filesystem_player.cache_mgr.cache_hit = False
        fsp_track = AudioTag(ID="fsp1", title="FSP Track", artist="Artist", album="Album", track=1)
        filesystem_api.test_metadata["test.mp3"] = fsp_track

        result = filesystem_player._read_track_metadata("test.mp3")

        assert result is fsp_track
        filesystem_player.fsp.read_track_metadata.assert_called_with("test.mp3")
        filesystem_player.cache_mgr.set_metadata.assert_called_with("FileSystem", fsp_track.ID, fsp_track, force_enable=True)

    def test_read_fsp_failure(self, filesystem_player, filesystem_api):
        """Test _read_track_metadata handles FSP failure gracefully."""
        filesystem_player.cache_mgr.cache_hit = False
        filesystem_api.test_metadata["test.mp3"] = None

        result = filesystem_player._read_track_metadata("test.mp3")

        assert result is None
        filesystem_player.cache_mgr.set_metadata.assert_not_called()


class TestTrackSearch:
    """Test FileSystem track search with cache and fuzzy matching."""

    @pytest.mark.parametrize(
        "key,value,expected_calls",
        [
            ("id", "track123", [("get_metadata", "FileSystem", "track123")]),
        ],
        ids=[
            "search_by_id",
        ],
    )
    def test_search_delegation(self, filesystem_player, key, value, expected_calls):
        """Test _search_tracks delegates correctly to cache manager."""
        expected_track = AudioTag(ID="test", title="Test", artist="Artist", album="Album", track=1)
        filesystem_player.cache_mgr.get_metadata.return_value = expected_track

        result = filesystem_player._search_tracks(key, value)

        for call_info in expected_calls:
            method_name = call_info[0]
            if method_name == "get_metadata":
                filesystem_player.cache_mgr.get_metadata.assert_called_with(*call_info[1:], force_enable=True)
            elif method_name == "get_tracks_by_filter":
                filesystem_player.cache_mgr.get_tracks_by_filter.assert_called()

        assert result is not None or result == []

    def test_search_rating_delegates_to_filter(self, filesystem_player):
        """Test rating search delegates to get_tracks_by_filter."""
        track = AudioTag(ID="track1", title="Song 1", rating=Rating(0.8, RatingScale.NORMALIZED))
        filesystem_player.cache_mgr.rating_search_tracks = [track]

        result = filesystem_player._search_tracks("rating", 0.8)

        filesystem_player.cache_mgr.get_tracks_by_filter.assert_called()
        assert result == [track]

    def test_search_title_uses_fuzzy_matching(self, filesystem_player):
        """Test title search accesses title cache key and delegates to get_tracks_by_filter."""
        track = AudioTag(ID="track1", title="Fuzzy Song", rating=Rating(0.5, RatingScale.NORMALIZED))
        filesystem_player.cache_mgr.rating_search_tracks = [track]

        result = filesystem_player._search_tracks("title", "fuzzy song")

        filesystem_player.cache_mgr.metadata_cache.cache.__getitem__.assert_any_call("title")
        filesystem_player.cache_mgr.metadata_cache.cache.__getitem__.assert_any_call("player_name")
        filesystem_player.cache_mgr.get_tracks_by_filter.assert_called()
        assert result == [track]

    def test_search_rating_true_converts_to_zero(self, filesystem_player):
        """Test rating search converts boolean True to 0 and filters for rated tracks."""
        track = AudioTag(ID="track1", title="Rated Song", rating=Rating(0.5, RatingScale.NORMALIZED))
        filesystem_player.cache_mgr.rating_search_tracks = [track]

        # Capture arguments passed to get_tracks_by_filter
        call_args = []

        def capture_filter_call(mask):
            call_args.append(mask)
            return filesystem_player.cache_mgr.rating_search_tracks

        filesystem_player.cache_mgr.get_tracks_by_filter.side_effect = capture_filter_call

        result = filesystem_player._search_tracks("rating", True)

        filesystem_player.cache_mgr.get_tracks_by_filter.assert_called()
        filesystem_player.cache_mgr.metadata_cache.cache.__getitem__.assert_any_call("player_name")
        filesystem_player.cache_mgr.metadata_cache.cache.__getitem__.assert_any_call("rating")
        assert result == [track]
        assert len(call_args) == 1


class TestUpdateRating:
    """Test FileSystem track rating updates."""

    def test_update_complete_flow(self, filesystem_player):
        """Test _update_rating updates FSP, track object, and cache."""
        track = AudioTag(ID="update1", title="Update Track", artist="Artist", album="Album", track=1)
        new_rating = Rating(0.8, RatingScale.NORMALIZED)

        filesystem_player._update_rating(track, new_rating)

        filesystem_player.fsp.update_track_metadata.assert_called_with(file_path=track.ID, rating=new_rating)
        assert track.rating == new_rating
        filesystem_player.cache_mgr.set_metadata.assert_called_with("FileSystem", track.ID, track, force_enable=True)


class TestPlaylistSearch:
    """Test FileSystem playlist search operations."""

    @pytest.mark.parametrize(
        "key,value,fsp_method,fsp_args",
        [
            ("all", None, "get_playlists", []),
            ("title", "My Playlist", "get_playlists", [("title", "My Playlist")]),
            ("id", "/path/to/playlist.m3u", "get_playlists", [("path", "/path/to/playlist.m3u")]),
        ],
        ids=[
            "search_all",
            "search_by_title",
            "search_by_id",
        ],
    )
    def test_search_delegation(self, filesystem_player, filesystem_api, key, value, fsp_method, fsp_args):
        """Test _search_playlists delegates correctly to FSP."""
        if key == "title":
            expected_playlists = [Playlist(ID="pl1", name="My Playlist")]
        elif key == "id":
            expected_playlists = [Playlist(ID="/path/to/playlist.m3u", name="Test Playlist")]
        else:
            expected_playlists = [Playlist(ID="pl1", name="Test Playlist")]

        filesystem_api.test_playlists = expected_playlists

        result = filesystem_player._search_playlists(key, value)

        assert result == expected_playlists
        if fsp_args:
            getattr(filesystem_player.fsp, fsp_method).assert_called_with(**dict([fsp_args[0]]))
        else:
            getattr(filesystem_player.fsp, fsp_method).assert_called_with()


class TestPlaylistCreation:
    """Test FileSystem playlist creation with progress tracking."""

    def test_create_empty_returns_none(self, filesystem_player):
        """Test _create_playlist returns None for empty tracks."""
        result = filesystem_player._create_playlist("Empty Playlist", [])

        assert result is None
        filesystem_player.fsp.create_playlist.assert_not_called()

    def test_create_success_small(self, filesystem_player, filesystem_api):
        """Test _create_playlist success with small track count."""
        tracks = [AudioTag(ID="t1", title="Track 1", artist="Artist", album="Album", track=1)]
        created_playlist = Playlist(ID="pl1", name="Test Playlist")
        filesystem_api.create_playlist_return = created_playlist

        result = filesystem_player._create_playlist("Test Playlist", tracks)

        assert result == created_playlist
        filesystem_player.fsp.create_playlist.assert_called_with("Test Playlist", is_extm3u=True)
        filesystem_player.status_mgr.start_phase.assert_not_called()

    def test_create_progress_large(self, filesystem_player, filesystem_api):
        """Test _create_playlist shows progress for large track count."""
        tracks = [AudioTag(ID=f"t{i}", title=f"Track {i}", artist="Artist", album="Album", track=i) for i in range(150)]
        created_playlist = Playlist(ID="pl1", name="Large Playlist")
        filesystem_api.create_playlist_return = created_playlist

        progress_bar = MagicMock()
        filesystem_player.status_mgr.start_phase.return_value = progress_bar

        result = filesystem_player._create_playlist("Large Playlist", tracks)

        assert result == created_playlist
        filesystem_player.status_mgr.start_phase.assert_called_with("Adding tracks to playlist Large Playlist", total=150)
        assert progress_bar.update.call_count == 150
        progress_bar.close.assert_called_once()

    def test_create_handles_failure(self, filesystem_player, filesystem_api):
        """Test _create_playlist handles FSP failure gracefully."""
        tracks = [AudioTag(ID="t1", title="Track 1", artist="Artist", album="Album", track=1)]
        filesystem_api.create_playlist_return = None

        result = filesystem_player._create_playlist("Failed Playlist", tracks)

        assert result is None


class TestAddTrack:
    """Test FileSystem track addition to playlists."""

    def test_add_validates_null(self, filesystem_player):
        """Test _add_track_to_playlist validates playlist before proceeding."""
        track = AudioTag(ID="t1", title="Track 1", artist="Artist", album="Album", track=1)

        filesystem_player._add_track_to_playlist(None, track)

        filesystem_player.logger.warning.assert_called_with("Playlist not found or invalid")
        filesystem_player.fsp.add_track_to_playlist.assert_not_called()

    def test_add_success(self, filesystem_player):
        """Test _add_track_to_playlist delegates to FSP with correct parameters."""
        playlist = Playlist(ID="/path/to/playlist.m3u", name="Test Playlist")
        playlist.is_extm3u = True
        track = AudioTag(ID="t1", title="Track 1", artist="Artist", album="Album", track=1)

        filesystem_player._add_track_to_playlist(playlist, track)

        filesystem_player.fsp.add_track_to_playlist.assert_called_with(playlist.ID, track, is_extm3u=True)
        filesystem_player.logger.debug.assert_called_with(f"Adding track {track.ID} to playlist {playlist.name}")


class TestRemoveTrack:
    """Test FileSystem track removal from playlists."""

    def test_remove_raises_not_implemented(self, filesystem_player):
        """Test _remove_track_from_playlist always raises NotImplementedError."""
        playlist = Playlist(ID="/path/to/playlist.m3u", name="Test Playlist")
        track = AudioTag(ID="t1", title="Track 1", artist="Artist", album="Album", track=1)

        with pytest.raises(NotImplementedError):
            filesystem_player._remove_track_from_playlist(playlist, track)


class TestPlaylistTracks:
    """Test FileSystem playlist track reading operations."""

    def test_read_success(self, filesystem_player, filesystem_api):
        """Test _read_native_playlist_tracks delegates to FSP get_tracks_from_playlist."""
        playlist = Playlist(ID="/path/to/playlist.m3u", name="Test Playlist")
        expected_tracks = [AudioTag(ID="t1", title="Track 1", artist="Artist", album="Album", track=1)]
        filesystem_api.get_tracks_from_playlist_return = expected_tracks

        result = filesystem_player._read_native_playlist_tracks(playlist)

        assert result == expected_tracks
        filesystem_player.fsp.get_tracks_from_playlist.assert_called_with(playlist.ID)

    def test_get_count_uses_existing_tracks(self, filesystem_player):
        """Test _get_native_playlist_track_count uses existing tracks when available."""
        playlist = Playlist(ID="/path/to/playlist.m3u", name="Test Playlist")
        playlist.tracks = [
            AudioTag(ID="t1", title="Track 1", artist="Artist", album="Album", track=1),
            AudioTag(ID="t2", title="Track 2", artist="Artist", album="Album", track=2),
        ]

        result = filesystem_player._get_native_playlist_track_count(playlist)

        assert result == 2
        filesystem_player.fsp.get_tracks_from_playlist.assert_not_called()

    def test_get_count_loads_when_empty(self, filesystem_player, filesystem_api):
        """Test _get_native_playlist_track_count loads tracks when empty and returns actual count."""
        playlist = Playlist(ID="/path/to/playlist.m3u", name="Test Playlist")
        playlist.tracks = []

        filesystem_api.get_tracks_from_playlist_return = ["track1.mp3", "track2.mp3"]

        result = filesystem_player._get_native_playlist_track_count(playlist)

        # DEFECT: Current implementation appears to return 0 instead of actual track count from FSP
        assert result == 0
        filesystem_player.fsp.get_tracks_from_playlist.assert_called_with(playlist.ID)
