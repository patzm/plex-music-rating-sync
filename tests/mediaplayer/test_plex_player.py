from contextlib import nullcontext
from unittest.mock import MagicMock, call

import pytest
from plexapi.exceptions import BadRequest, NotFound

from ratings import Rating, RatingScale
from sync_items import Playlist


class TestPlexConnect:
    @pytest.mark.parametrize(
        "password,token,account_return_error,expect_exit",
        [
            ("pass", None, None, False),
            (None, "token", None, False),
            ("pass", None, NotFound("not found"), True),
            ("pass", None, BadRequest("bad request"), True),
        ],
        ids=[
            "password_success",
            "token_success",
            "notfound_retries_and_exit",
            "badrequest_retries_and_exit",
        ],
    )
    def test_authenticate(self, plex_player, plex_api, password, token, account_return_error, expect_exit):
        plex_api.account_raise = account_return_error
        if expect_exit:
            with pytest.raises(SystemExit):
                plex_player._authenticate("server", "user", password, token)
        else:
            result = plex_player._authenticate("server", "user", password, token)
            assert isinstance(result, MagicMock)

    @pytest.mark.parametrize(
        "server,username,password,token,expect_exception,exception_type,exception_msg",
        [
            (None, "user", "pass", None, True, ValueError, "Plex server and username are required for Plex player"),
            ("server", None, "pass", None, True, ValueError, "Plex server and username are required for Plex player"),
            ("server", "user", None, None, True, ValueError, "Plex token or password is required for Plex player"),
            ("server", "user", "pass", "token", False, None, None),
        ],
        ids=[
            "missing_server",
            "missing_username",
            "missing_credentials",
            "all_present",
        ],
    )
    def test_connect_input_validation(self, plex_player, server, username, password, token, expect_exception, exception_type, exception_msg, plex_api):
        plex_player.config_mgr.server = server
        plex_player.config_mgr.username = username
        plex_player.config_mgr.passwd = password
        plex_player.config_mgr.token = token
        plex_player.account.resource("mock_server").connect.reset_mock()
        plex_api.resource.connect.reset_mock()
        if expect_exception:
            with pytest.raises(exception_type) as e:
                plex_player.connect()
            assert exception_msg in str(e.value)
            assert plex_player.account.resource("mock_server").connect.call_count == 0
        else:
            plex_player.connect()
            assert plex_player.account.resource("mock_server").connect.call_count == 1

    @pytest.mark.parametrize(
        "library_count, simulate_input, expected_key, expect_exit",
        [
            (0, None, None, True),
            (1, None, 1, False),
            (2, "1", 1, False),
            (2, "bad\n2", 2, False),
        ],
        ids=[
            "no_libraries",
            "one_library",
            "multiple_valid",
            "multiple_retry_then_valid",
        ],
    )
    def test_connect_library_selection(self, plex_player, plex_api, monkeypatch, library_count, simulate_input, expected_key, expect_exit):
        plex_api.library_count = library_count

        if simulate_input is not None:
            inputs = simulate_input.splitlines()
            monkeypatch.setattr("builtins.input", lambda _: inputs.pop(0))

        context = pytest.raises(SystemExit) if expect_exit else nullcontext()

        with context:
            plex_player.connect()

        if not expect_exit:
            assert plex_player.music_library.key == expected_key

    def test_connect_resource_failure(self, plex_player, plex_api):
        """Test connect logs error and raises SystemExit when resource connection raises NotFound."""
        plex_api.connect_raise = NotFound("Server not found")
        with pytest.raises(SystemExit):
            plex_player.connect()

        plex_player.logger.error.assert_called_with(f"Failed to connect to {plex_player.name()}")


class TestPlexPlaylistSearch:
    @pytest.mark.parametrize(
        "key,value,return_native,setup_map,expect_error,expect_empty",
        [
            ("all", None, False, {}, False, False),
            ("title", "My Playlist", False, {"my playlist": 1000}, False, False),
            ("id", 1000, False, {}, False, False),
            ("badkey", None, False, {}, True, False),
            ("id", 9999, False, {}, False, True),
        ],
        ids=[
            "search_all_returns_list",
            "search_by_title_finds_playlist",
            "search_by_id_finds_playlist",
            "search_invalid_key_raises_error",
            "search_nonexistent_id_returns_empty",
        ],
    )
    def test_search_playlists_parameters(self, plex_player, plex_api, key, value, return_native, setup_map, expect_error, expect_empty, plex_playlist_factory):
        """Test playlist search handles different search parameters correctly."""
        plex_player._title_to_id_map = {k.lower(): v for k, v in setup_map.items()}

        if value == 1000:
            playlist = Playlist(ID=1000, name="My Playlist")
            native_playlist = plex_playlist_factory(playlist)
            plex_api.test_playlists = [native_playlist]

        if expect_error:
            with pytest.raises(ValueError):
                plex_player.search_playlists(key, value, return_native)
        else:
            result = plex_player.search_playlists(key, value, return_native)
            if expect_empty:
                assert result == []
            else:
                assert isinstance(result, list)

    @pytest.mark.parametrize(
        "is_auto_playlist,num_items,expect_status_bar",
        [
            (True, 2, False),
            (False, 2, False),
            (False, 101, True),
        ],
        ids=[
            "auto_playlist_no_status_bar",
            "manual_playlist_few_tracks_no_status_bar",
            "manual_playlist_many_tracks_shows_status_bar",
        ],
    )
    def test_load_playlist_tracks_progress(self, plex_player, plex_api, is_auto_playlist, num_items, expect_status_bar, track_factory, plex_playlist_factory, plex_track_factory):
        """Test that progress bar appears only for large manual playlists."""
        playlist = Playlist(ID=1000, name="Test")
        playlist.is_auto_playlist = is_auto_playlist

        test_tracks = [track_factory(ID=i) for i in range(1, num_items + 1)]
        native_tracks = [plex_track_factory(track) for track in test_tracks]

        native_playlist = plex_playlist_factory(playlist)
        native_playlist.items.return_value = native_tracks

        plex_api.test_playlists = [native_playlist]
        plex_player.load_playlist_tracks(playlist)

        if expect_status_bar:
            plex_player.status_mgr.start_phase.assert_called_once()
        else:
            plex_player.status_mgr.start_phase.assert_not_called()

    def test_read_native_playlist_tracks(self, plex_player, track_factory, plex_playlist_factory, plex_track_factory):
        """Test reading tracks from native playlist converts all tracks to AudioTag objects."""
        # Create test tracks and convert to native format
        test_tracks = [track_factory(ID=1, title="Track 1"), track_factory(ID=2, title="Track 2")]
        native_tracks = [plex_track_factory(track) for track in test_tracks]

        # Create native playlist
        native_playlist = plex_playlist_factory(Playlist(ID=1001, name="Test"))
        native_playlist.items.return_value = native_tracks

        result = plex_player._read_native_playlist_tracks(native_playlist)

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(hasattr(track, "title") and hasattr(track, "artist") for track in result)

    def test_get_native_playlist_track_count(self, plex_player, track_factory, plex_playlist_factory, plex_track_factory):
        """Test counting tracks in native playlist returns accurate number."""
        # Create 3 test tracks
        test_tracks = [track_factory(ID=i, title=f"Track {i}") for i in range(1, 4)]
        native_tracks = [plex_track_factory(track) for track in test_tracks]

        # Create native playlist
        native_playlist = plex_playlist_factory(Playlist(ID=1002, name="Test"))
        native_playlist.items.return_value = native_tracks

        result = plex_player._get_native_playlist_track_count(native_playlist)

        assert result == 3

    def test_search_playlists_native_return(self, plex_player, plex_api, plex_playlist_factory):
        """Test searching playlists with return_native=True returns Plex API objects."""
        playlist = Playlist(ID=1003, name="My Playlist")
        native_playlist = plex_playlist_factory(playlist)
        plex_api.test_playlists = [native_playlist]

        result = plex_player.search_playlists("id", 1003, True)

        assert isinstance(result, list)
        assert len(result) == 1
        assert hasattr(result[0], "key")  # Native Plex objects have key attribute

    def test_collect_playlists_filters_non_audio_playlists(self, plex_player, plex_api, plex_playlist_factory):
        """Test that non-audio playlists are filtered out by _collect_playlists method."""
        # Create an audio playlist (should be included)
        native_audio_playlist = plex_playlist_factory(Playlist(ID=1000, name="Music Playlist"))
        native_video_playlist = plex_playlist_factory(Playlist(ID=1001, name="Video Playlist"))
        native_video_playlist.playlistType = "video"

        plex_api.test_playlists = [native_audio_playlist, native_video_playlist]
        result = plex_player.search_playlists("all")

        assert len(result) == 1
        assert result[0].name == "Music Playlist"
        assert result[0].ID == "1000"

    def test_get_playlists_delegates_to_search_all(self, plex_player, plex_api, plex_playlist_factory):
        """Test _get_playlists method logs info message and delegates to search_playlists('all')."""
        # Create test playlists
        playlist1 = Playlist(ID=1000, name="Test Playlist 1")
        playlist2 = Playlist(ID=1001, name="Test Playlist 2")
        native_playlist1 = plex_playlist_factory(playlist1)
        native_playlist2 = plex_playlist_factory(playlist2)
        plex_api.test_playlists = [native_playlist1, native_playlist2]

        # Call the method
        result = plex_player._get_playlists()

        # Verify logger was called with expected message
        plex_player.logger.info.assert_called_once_with("Reading playlists from the Plex player")

        assert len(result) == 2
        assert {playlist.name for playlist in result} == {"Test Playlist 1", "Test Playlist 2"}

    def test_load_playlist_tracks_missing_playlist_returns(self, plex_player, plex_api):
        """Test load_playlist_tracks with nonexistent playlist logs warning and returns early."""
        playlist = Playlist(ID=999, name="Nonexistent Playlist")
        plex_player._title_to_id_map = {}
        plex_api.test_playlists = []

        initial_track_count = len(playlist.tracks)
        assert initial_track_count == 0

        plex_player.load_playlist_tracks(playlist)

        plex_player.logger.warning.assert_called_once_with(f"Native playlist not found for {playlist.name}")


class TestPlexPlaylistUpdate:
    def test_add_track_to_playlist(self, plex_player, plex_api, track_factory, plex_playlist_factory, plex_track_factory):
        """Test adding track to playlist calls native addItems method correctly."""
        playlist = Playlist(ID=1002, name="Test")
        track = track_factory(ID=1)

        # Create native playlist and track
        native_playlist = plex_playlist_factory(playlist)
        native_track = plex_track_factory(track)

        # Setup mock to track addItems calls
        native_playlist.addItems = MagicMock()

        # Register with API mock
        plex_api.test_playlists = [native_playlist]
        plex_api.test_tracks = [native_track]

        plex_player._add_track_to_playlist(playlist, track)

        # Verify addItems was called with correct track
        native_playlist.addItems.assert_called_once_with(native_track)

    def test_remove_track_from_playlist(self, plex_player, plex_api, track_factory, plex_playlist_factory, plex_track_factory):
        """Test removing track from playlist calls native removeItem method correctly."""
        playlist = Playlist(ID=1003, name="Test")
        track = track_factory(ID=1)

        # Create native playlist and track
        native_playlist = plex_playlist_factory(playlist)
        native_track = plex_track_factory(track)

        # Setup mock to track removeItem calls
        native_playlist.removeItem = MagicMock()

        # Register with API mock
        plex_api.test_playlists = [native_playlist]
        plex_api.test_tracks = [native_track]

        plex_player._remove_track_from_playlist(playlist, track)

        # Verify removeItem was called with correct track
        native_playlist.removeItem.assert_called_once_with(native_track)

    def test_remove_track_from_nonexistent_playlist(self, plex_player, plex_api, track_factory):
        """Test removing track from nonexistent playlist logs warning and performs no operation."""
        playlist = Playlist(ID=9999, name="Missing")
        track = track_factory(ID=1)

        # Ensure no playlists exist in API mock
        plex_api.test_playlists = []

        # Mock logger to verify warning
        plex_player.logger.warning = MagicMock()

        plex_player._remove_track_from_playlist(playlist, track)

        # Verify warning was logged (should be the final call)
        expected_calls = [call("Failed to retrieve playlist by ID '9999': search error"), call("Native playlist not found for Missing")]
        plex_player.logger.warning.assert_has_calls(expected_calls)

    def test_change_track_not_found(self, plex_player, plex_api, track_factory, plex_playlist_factory):
        """Test adding nonexistent track to playlist logs warning and performs no operation."""
        playlist = Playlist(ID=1004, name="Test")
        track = track_factory(ID=999)  # Use non-existent integer ID

        # Create playlist but no tracks
        native_playlist = plex_playlist_factory(playlist)
        native_playlist.addItems = MagicMock()
        plex_api.test_playlists = [native_playlist]
        plex_api.test_tracks = []

        with pytest.raises(RuntimeError):
            plex_player._add_track_to_playlist(playlist, track)
            plex_player.logger.error.assert_called_once_with("Failed to search for track 999")
            native_playlist.addItems.assert_not_called()

        with pytest.raises(RuntimeError):
            plex_player._remove_track_from_playlist(playlist, track)
            plex_player.logger.error.assert_called_once_with("Failed to search for track 999")
            native_playlist.removeItem.assert_not_called()

    def test_add_track_missing_playlist_warns(self, plex_player, plex_api, track_factory):
        """Test adding track to missing playlist logs warning and returns early."""
        playlist = Playlist(ID=9999, name="Missing Playlist")
        track = track_factory(ID=1)

        # Ensure no playlists exist in API mock (simulates empty search results)
        plex_api.test_playlists = []

        # Mock logger to verify warning
        plex_player.logger.warning = MagicMock()

        # Execute the method
        plex_player._add_track_to_playlist(playlist, track)

        # Verify warning was logged with exact message
        plex_player.logger.warning.assert_called_with("Native playlist not found for Missing Playlist")


class TestPlexPlaylistCreation:
    def test_create_playlist_all_tracks_success(self, plex_player, track_factory, plex_playlist_factory, plex_track_factory, plex_api):
        """Test creating a playlist when all tracks are found: API called with correct args, playlist state unchanged."""
        track1 = track_factory(ID=1)
        track2 = track_factory(ID=2)
        native_track1 = plex_track_factory(track1)
        native_track2 = plex_track_factory(track2)
        plex_api.test_tracks = [native_track1, native_track2]
        playlist_obj = plex_playlist_factory(Playlist(ID=1000, name="foo"))
        plex_api.test_playlists.append(playlist_obj)
        plex_player.plex_api_connection.createPlaylist.reset_mock()

        plex_player._create_playlist("foo", [track1, track2])
        plex_player.plex_api_connection.createPlaylist.assert_called_once_with(title="foo", items=[native_track1, native_track2])

    def test_create_playlist_some_tracks_success(self, plex_player, track_factory, plex_playlist_factory, plex_track_factory, plex_api):
        """Test creating a playlist when some tracks are missing: API called with found tracks only."""
        track1 = track_factory(ID=1)
        missing_track = track_factory(ID=999)
        native_track1 = plex_track_factory(track1)
        plex_api.test_tracks = [native_track1]
        playlist_obj = plex_playlist_factory(Playlist(ID=1001, name="foo"))
        plex_api.test_playlists.append(playlist_obj)
        plex_player.plex_api_connection.createPlaylist.reset_mock()

        plex_player._create_playlist("foo", [track1, missing_track])
        plex_player.plex_api_connection.createPlaylist.assert_called_once_with(title="foo", items=[native_track1])

    def test_create_playlist_no_tracks_noop(self, plex_player, plex_api):
        """Test creating a playlist with no tracks: API not called, playlist state unchanged."""
        plex_api.test_tracks = []
        plex_player.plex_api_connection.createPlaylist.reset_mock()

        result = plex_player._create_playlist("foo", [])
        plex_player.plex_api_connection.createPlaylist.assert_not_called()
        assert result is None

    def test_create_playlist_tracks_not_found_noop(self, plex_player, track_factory, plex_api):
        """Test creating a playlist when no tracks are found: API not called, playlist state unchanged."""
        track1 = track_factory(ID=1)
        plex_api.test_tracks = []
        plex_player.plex_api_connection.createPlaylist.reset_mock()

        result = plex_player._create_playlist("foo", [track1])
        plex_player.plex_api_connection.createPlaylist.assert_not_called()
        assert result is None

    def test_create_playlist_track_search_raises(self, plex_player, track_factory, plex_api):
        """Test exception during track search: error is logged, API not called, playlist state unchanged."""
        track = track_factory(ID=1)
        plex_api.test_tracks = [track]
        plex_player.plex_api_connection.createPlaylist.reset_mock()

        plex_player._create_playlist("foo", [track])
        plex_player.plex_api_connection.createPlaylist.assert_not_called()
        plex_player.logger.error.assert_any_call("Failed to search for track 1: search error")


class TestPlexTrackSearch:
    @pytest.mark.parametrize(
        "key,value",
        [
            ("title", "Title"),
            ("id", 1),
            ("rating", True),
        ],
        ids=[
            "search_by_title_found",
            "search_by_id_found",
            "search_by_rating_found",
        ],
    )
    def test_search_tracks_valid_keys(self, plex_player, plex_api, track_factory, plex_track_factory, key, value):
        """Test track search for valid keys, found cases, non-native returns only."""
        test_track = track_factory(ID=1, title="Title", rating=0.5)
        native_track = plex_track_factory(test_track)
        plex_api.test_tracks = [native_track]

        result = plex_player._search_tracks(key, value)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_search_tracks_not_found_raises(self, plex_player, plex_api):
        """Test searching by id returns empty list when track is not found."""
        plex_api.test_tracks = []
        with pytest.raises(RuntimeError):
            plex_player._search_tracks("id", 999, False)

    def test_search_tracks_invalid_key_raises(self, plex_player):
        """Test searching tracks with an invalid key raises KeyError."""
        with pytest.raises(KeyError):
            plex_player._search_tracks("badkey", None, False)

    def test_search_tracks_native_return(self, plex_player, plex_api, track_factory, plex_track_factory):
        """Test searching tracks with return_native=True returns Plex API objects."""
        test_track = track_factory(ID=1, title="Title")
        native_track = plex_track_factory(test_track)
        plex_api.test_tracks = [native_track]

        result = plex_player._search_tracks("id", 1, True)
        assert hasattr(result[0], "key")

        result = plex_player._search_tracks("id", 1, False)
        assert not hasattr(result[0], "key")

    @pytest.mark.parametrize(
        "num_tracks,expect_progress_bar",
        [
            (499, False),
            (501, True),
        ],
        ids=[
            "below_progress_threshold",
            "above_progress_threshold",
        ],
    )
    def test_search_tracks_large_collection_progress_bar(self, plex_player, plex_api, track_factory, plex_track_factory, num_tracks, expect_progress_bar):
        """Test progress bar appears for large track collections (≥500 tracks)."""
        # Create large collection of tracks
        test_tracks = [track_factory(ID=i, title=f"Track {i}") for i in range(1, num_tracks + 1)]
        native_tracks = [plex_track_factory(track) for track in test_tracks]
        plex_api.test_tracks = native_tracks
        plex_player._search_tracks("rating", 0)

        if expect_progress_bar:
            plex_player.status_mgr.start_phase.assert_called_once_with(f"Reading track metadata from {plex_player.name()}", total=num_tracks)
        else:
            plex_player.status_mgr.start_phase.assert_not_called()


class TestPlexTrackUpdate:
    def test_read_track_metadata(self, plex_player, track_factory, plex_track_factory):
        """Test reading all fields from a track's metadata."""
        track = track_factory(ID=1, artist="Artist")
        native_track = plex_track_factory(track)
        result = plex_player._read_track_metadata(native_track)
        assert result.artist == "Artist"

    def test_update_rating_success(self, plex_player, plex_api, track_factory, plex_track_factory):
        """Test updating the rating of a track succeeds and calls edit."""
        track = track_factory(ID=1)
        native_track = plex_track_factory(track)
        plex_api.test_tracks = [native_track]
        called = {"edit": False}

        def edit_side_effect(*args, **kwargs):
            called["edit"] = True

        native_track.edit = edit_side_effect
        plex_player.music_library.fetchItem.side_effect = lambda v: native_track if (v == 1 or v == "1") else None
        plex_player._update_rating(native_track, Rating(5, scale=RatingScale.ZERO_TO_TEN))
        assert called["edit"]

    def test_update_rating_edit_fails(self, plex_player, plex_api, track_factory, plex_track_factory):
        """Test updating the rating of a track raises and does not call edit."""
        track = track_factory(ID=1)
        native_track = plex_track_factory(track)
        plex_api.test_tracks = [native_track]
        called = {"edit": False}

        def edit_side_effect(*args, **kwargs):
            called["edit"] = True
            raise RuntimeError("fail")

        native_track.edit = edit_side_effect
        plex_player.music_library.fetchItem.side_effect = lambda v: native_track if (v == 1 or v == "1") else None
        import pytest

        with pytest.raises(RuntimeError):
            plex_player._update_rating(native_track, Rating(5, scale=RatingScale.ZERO_TO_TEN))
        assert called["edit"]

    def test_update_rating_track_not_found(self, plex_player, plex_api, track_factory, plex_track_factory):
        """Test updating the rating of a track that is not found does not call edit and raises."""
        track = track_factory(ID=1)
        plex_api.test_tracks = []

        with pytest.raises((RuntimeError, ValueError, KeyError)):
            plex_player._update_rating(track, Rating(5, scale=RatingScale.ZERO_TO_TEN))
        plex_player.logger.error.assert_called_once_with(f"Failed to update rating on {track}: search error")
