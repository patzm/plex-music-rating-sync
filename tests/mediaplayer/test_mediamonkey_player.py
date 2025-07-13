"""MediaMonkey player tests with COM interface mocks."""

from unittest.mock import MagicMock

import pytest

from ratings import Rating, RatingScale
from sync_items import AudioTag, Playlist


class TestConnect:
    def test_connect_success(self, mm_player):
        mm_player.connect()
        mm_player.logger.info.assert_called()

    def test_connect_raises(self, mm_player, mm_api):
        mm_api.connect_raise = RuntimeError("Connection failed")
        with pytest.raises(RuntimeError, match="Connection failed"):
            mm_player.connect()
        mm_player.logger.error.assert_called()


class TestPlaylistSearch:
    @pytest.mark.parametrize(
        "key,value,return_native,setup_playlists,expect_error,expect_empty",
        [
            ("all", None, False, [{"ID": 1, "Title": "My Playlist"}, {"ID": 2, "Title": "Another Playlist"}], False, False),
            ("title", "My Playlist", False, [{"ID": 1, "Title": "My Playlist"}], False, False),
            ("id", 1, False, [{"ID": 1, "Title": "My Playlist"}], False, False),
            ("badkey", None, False, [], True, False),
            ("id", 9999, False, [{"ID": 1, "Title": "My Playlist"}], False, True),
            ("title", "Nonexistent", False, [{"ID": 1, "Title": "My Playlist"}], False, True),
        ],
        ids=[
            "search_all_returns_multiple_playlists",
            "search_by_title_finds_matching_playlist",
            "search_by_id_finds_matching_playlist",
            "search_invalid_key_raises_error",
            "search_nonexistent_id_returns_empty",
            "search_nonexistent_title_returns_empty",
        ],
    )
    def test_search_parametrized_queries(self, mm_player, mm_playlist_factory, key, value, return_native, setup_playlists, expect_error, expect_empty):
        playlists = [mm_playlist_factory(**pl_data) for pl_data in setup_playlists]
        mm_player.sdb.set_playlists(playlists)

        if expect_error:
            with pytest.raises(ValueError, match="Invalid search key"):
                mm_player.search_playlists(key, value, return_native)
        else:
            result = mm_player.search_playlists(key, value, return_native)
            assert isinstance(result, list)

            if expect_empty:
                assert len(result) == 0
            else:
                assert len(result) > 0
                if key == "all":
                    assert len(result) == len(setup_playlists)
                elif key in ["title", "id"]:
                    assert len(result) == 1
                    if return_native:
                        assert hasattr(result[0], "ID")
                        assert hasattr(result[0], "Title")
                    else:
                        assert hasattr(result[0], "name")
                        assert hasattr(result[0], "ID")

    @pytest.mark.parametrize(
        "return_native,playlist_id,title,expected_attributes",
        [
            (True, 1003, "Test Playlist", ["ID", "Title"]),
            (False, 1004, "Converted Playlist", ["name", "ID"]),
        ],
        ids=["native_return", "converted_return"],
    )
    def test_search_return_native_parameter(self, mm_player, mm_playlist_factory, return_native, playlist_id, title, expected_attributes):
        playlist_data = {"ID": playlist_id, "Title": title}
        native_playlist = mm_playlist_factory(**playlist_data)
        mm_player.sdb.set_playlists([native_playlist])

        result = mm_player.search_playlists("id", playlist_id, return_native=return_native)

        assert isinstance(result, list)
        assert len(result) == 1

        playlist = result[0]
        for attr in expected_attributes:
            assert hasattr(playlist, attr)

        if return_native:
            assert getattr(playlist, "ID", None) == playlist_id
            assert getattr(playlist, "Title", None) == title
        else:
            assert playlist.ID == playlist_id
            assert playlist.name == title

    def test_search_title_case_insensitive(self, mm_player, mm_playlist_factory):
        playlist_data = {"ID": 1005, "Title": "CaseSensitive Playlist"}
        native_playlist = mm_playlist_factory(**playlist_data)
        mm_player.sdb.set_playlists([native_playlist])

        for search_title in ["casesensitive playlist", "CASESENSITIVE PLAYLIST", "CaseSensitive Playlist"]:
            result = mm_player.search_playlists("title", search_title)
            assert len(result) == 1
            assert result[0].name == "CaseSensitive Playlist"

    def test_search_nested_hierarchy(self, mm_player, mm_playlist_factory):
        playlists = [
            mm_playlist_factory(ID=1, Title="Parent"),
            mm_playlist_factory(ID=2, Title="Parent.Child"),
            mm_playlist_factory(ID=3, Title="Parent.Child.Grandchild"),
        ]
        mm_player.sdb.set_playlists(playlists)

        result = mm_player.search_playlists("title", "Parent.Child")
        assert len(result) == 1
        assert result[0].name == "Parent.Child"
        assert result[0].ID == 2

    def test_search_all_preserves_order(self, mm_player, mm_playlist_factory):
        playlists = [
            mm_playlist_factory(ID=3, Title="Third"),
            mm_playlist_factory(ID=1, Title="First"),
            mm_playlist_factory(ID=2, Title="Second"),
        ]
        mm_player.sdb.set_playlists(playlists)

        result = mm_player.search_playlists("all")
        assert len(result) == 3
        expected_order = ["Third", "First", "Second"]
        actual_order = [pl.name for pl in result]
        assert actual_order == expected_order

    def test_search_empty_returns_empty(self, mm_player):
        mm_player.sdb.set_playlists([])

        result = mm_player.search_playlists("all")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_convert_invalid_playlist_returns_none(self, mm_player):
        result = mm_player._convert_playlist(None, "test")
        assert result is None

        mock_playlist = mm_player.sdb._make_playlist(ID=1, Title="Test")
        result = mm_player._convert_playlist(mock_playlist, "")
        assert result is None

    def test_collect_nested_playlists_returns_flattened_list(self, mm_player, mm_playlist_factory):
        grandchild_playlist = mm_playlist_factory(ID=3, Title="Grandchild", children=[])
        child_playlist = mm_playlist_factory(ID=2, Title="Child", children=[grandchild_playlist])
        parent_playlist = mm_playlist_factory(ID=1, Title="Parent", children=[child_playlist])

        results = mm_player._collect_playlists(parent_playlist, "Root")

        assert isinstance(results, list)
        assert len(results) == 2

        playlist_titles = [title for _, title in results]
        assert "Root.Child" in playlist_titles
        assert "Root.Child.Grandchild" in playlist_titles

        playlist_objects = [pl for pl, _ in results]
        playlist_ids = [pl.ID for pl in playlist_objects]
        assert 2 in playlist_ids
        assert 3 in playlist_ids


class TestPlaylistCreation:
    def test_create_empty_title_raises(self, mm_player, mm_track_factory):
        tracks = [mm_track_factory(ID=1, Title="Test Track")]

        with pytest.raises(ValueError, match="Title and tracks cannot be empty"):
            mm_player._create_playlist("", tracks)

    def test_create_empty_tracks_raises(self, mm_player):
        with pytest.raises(ValueError, match="Title and tracks cannot be empty"):
            mm_player._create_playlist("Test Playlist", [])

    def test_create_simple_success(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Track One")
        track2 = mm_track_factory(ID=2, Title="Track Two")
        mm_player.sdb.set_tracks([track1, track2])

        audio_tracks = [
            AudioTag(ID="1", title="Track One", artist="Artist", album="Album", track=1, rating=Rating(0.8, RatingScale.NORMALIZED)),
            AudioTag(ID="2", title="Track Two", artist="Artist", album="Album", track=2, rating=Rating(0.6, RatingScale.NORMALIZED)),
        ]

        mm_player.status_mgr.start_phase.return_value = MagicMock()

        result = mm_player._create_playlist("Simple Playlist", audio_tracks)

        assert result is not None
        assert hasattr(result, "Title")
        assert result.Title == "Simple Playlist"

        mm_player.status_mgr.start_phase.assert_called_once_with("Adding tracks to playlist Simple Playlist", total=2)

    def test_create_nested_with_dots(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Nested Track")
        mm_player.sdb.set_tracks([track1])

        audio_tracks = [AudioTag(ID="1", title="Nested Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))]

        mm_player.status_mgr.start_phase.return_value = MagicMock()

        result = mm_player._create_playlist("Parent.Child.Grandchild", audio_tracks)

        assert result is not None
        assert hasattr(result, "Title")
        assert result.Title == "Grandchild"

    def test_create_existing_reused(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Existing Track")
        mm_player.sdb.set_tracks([track1])

        existing_playlist = mm_player.sdb._make_playlist(ID=100, Title="Existing Playlist")
        mm_player.sdb.set_playlists([existing_playlist])

        audio_tracks = [AudioTag(ID="1", title="Existing Track", artist="Artist", album="Album", track=1, rating=Rating(0.7, RatingScale.NORMALIZED))]

        mm_player.status_mgr.start_phase.return_value = MagicMock()

        result = mm_player._create_playlist("Existing Playlist", audio_tracks)

        assert result is not None
        assert result.ID == 100

    def test_create_track_not_found_logs(self, mm_player):
        mm_player.sdb.set_tracks([])

        audio_tracks = [AudioTag(ID="999", title="Missing Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))]

        mm_player.status_mgr.start_phase.return_value = MagicMock()

        empty_result = MagicMock()
        empty_result.Item = None
        mm_player.sdb.Database.QuerySongs.return_value = empty_result

        mm_player._create_playlist("Test Playlist", audio_tracks)

        mm_player.logger.warning.assert_called_with("Track with ID 999 not found in MediaMonkey database")

    def test_create_track_error_continues(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Problematic Track")
        mm_player.sdb.set_tracks([track1])

        audio_tracks = [AudioTag(ID="1", title="Problematic Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))]

        mm_player.status_mgr.start_phase.return_value = MagicMock()

        error_playlist = mm_player.sdb._make_playlist(ID=1, Title="Error Playlist")
        error_playlist.AddTrack = MagicMock(side_effect=RuntimeError("AddTrack failed"))

        mm_player.sdb.set_playlists([error_playlist])

        mm_player.sdb.QuerySongs_return = MagicMock()
        mm_player.sdb.QuerySongs_return.Item = track1

        result = mm_player._create_playlist("Error Playlist", audio_tracks)

        mm_player.logger.error.assert_called_with("Failed to add track ID 1 to playlist: AddTrack failed")
        assert result is not None

    @pytest.mark.parametrize(
        "track_count,expect_progress_bar",
        [
            (1, True),  # Even small playlists get progress bars in _create_playlist
            (150, True),
        ],
        ids=["single_track", "large_playlist"],
    )
    def test_create_uses_progress_bar(self, mm_player, mm_track_factory, track_count, expect_progress_bar):
        tracks = [mm_track_factory(ID=i, Title=f"Track {i}") for i in range(1, track_count + 1)]
        mm_player.sdb.set_tracks(tracks)

        audio_tracks = [
            AudioTag(ID=str(i), title=f"Track {i}", artist="Artist", album="Album", track=i, rating=Rating(0.5, RatingScale.NORMALIZED)) for i in range(1, track_count + 1)
        ]

        progress_bar_mock = MagicMock()
        mm_player.status_mgr.start_phase.return_value = progress_bar_mock

        result = mm_player._create_playlist("Progress Test", audio_tracks)

        if expect_progress_bar:
            mm_player.status_mgr.start_phase.assert_called_once_with("Adding tracks to playlist Progress Test", total=track_count)
            assert progress_bar_mock.update.call_count == track_count
            progress_bar_mock.close.assert_called_once()

        assert result is not None

    def test_create_integration_with_search(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Integration Track 1", ArtistName="Artist A")
        track2 = mm_track_factory(ID=2, Title="Integration Track 2", ArtistName="Artist B")
        mm_player.sdb.set_tracks([track1, track2])

        audio_tracks = [
            AudioTag(ID="1", title="Integration Track 1", artist="Artist A", album="Album", track=1, rating=Rating(0.8, RatingScale.NORMALIZED)),
            AudioTag(ID="2", title="Integration Track 2", artist="Artist B", album="Album", track=2, rating=Rating(0.6, RatingScale.NORMALIZED)),
        ]

        mm_player.status_mgr.start_phase.return_value = MagicMock()

        result = mm_player._create_playlist("Integration Test", audio_tracks)

        assert result is not None
        assert result.Title == "Integration Test"

        assert mm_player.sdb.Database.QuerySongs.call_count == 2

        expected_queries = ["ID=1", "ID=2"]
        actual_queries = [call.args[0] for call in mm_player.sdb.Database.QuerySongs.call_args_list]
        assert actual_queries == expected_queries


class TestGetPlaylists:
    def test_get_delegates_to_search_all(self, mm_player):
        # Mock search_playlists to verify method call
        mm_player.search_playlists = MagicMock(return_value=[])

        # Call method under test
        mm_player._get_playlists()

        # Verify search_playlists was called once with 'all'
        mm_player.search_playlists.assert_called_once_with("all")


class TestAddTrackToPlaylist:
    def test_add_success(self, mm_player, mm_playlist_factory, mm_track_factory):
        track = mm_track_factory(ID=1, Title="Test Track")
        native_playlist = mm_playlist_factory(ID=100, Title="Test Playlist", tracks=[])
        mm_player.sdb.set_playlists([native_playlist])
        mm_player.sdb.set_tracks([track])

        playlist = Playlist(ID=100, name="Test Playlist")
        audio_track = AudioTag(ID="1", title="Test Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._add_track_to_playlist(playlist, audio_track)

        assert len(native_playlist.AddTrack_calls) == 1
        assert native_playlist.AddTrack_calls[0].ID == 1

    def test_add_native_playlist_success(self, mm_player, mm_playlist_factory, mm_track_factory):
        track = mm_track_factory(ID=2, Title="Native Test Track")
        native_playlist = mm_playlist_factory(ID=200, Title="Native Playlist", tracks=[])
        mm_player.sdb.set_playlists([native_playlist])
        mm_player.sdb.set_tracks([track])

        audio_track = AudioTag(ID="2", title="Native Test Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._add_track_to_playlist(native_playlist, audio_track)

        assert len(native_playlist.AddTrack_calls) == 1
        assert native_playlist.AddTrack_calls[0].ID == 2

    def test_add_playlist_not_found_logs_warning(self, mm_player, mm_track_factory):
        track = mm_track_factory(ID=1, Title="Test Track")
        mm_player.sdb.set_playlists([])
        mm_player.sdb.set_tracks([track])

        playlist = Playlist(ID=999, name="Missing Playlist")
        audio_track = AudioTag(ID="1", title="Test Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._add_track_to_playlist(playlist, audio_track)

        mm_player.logger.warning.assert_called_with("Native playlist not found for Missing Playlist")

    def test_add_track_not_found_logs_warning(self, mm_player, mm_playlist_factory):
        native_playlist = mm_playlist_factory(ID=100, Title="Test Playlist", tracks=[])
        mm_player.sdb.set_playlists([native_playlist])
        mm_player.sdb.set_tracks([])

        playlist = Playlist(ID=100, name="Test Playlist")
        audio_track = AudioTag(ID="999", title="Missing Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._add_track_to_playlist(playlist, audio_track)

        mm_player.logger.warning.assert_called_with("Could not find track for: Artist - Album - Missing Track in MediaMonkey")

    def test_add_uses_return_native_for_track_search(self, mm_player, mm_playlist_factory, mm_track_factory):
        track = mm_track_factory(ID=1, Title="Search Track")
        native_playlist = mm_playlist_factory(ID=100, Title="Search Playlist", tracks=[])
        mm_player.sdb.set_playlists([native_playlist])
        mm_player.sdb.set_tracks([track])

        # Spy on search_tracks to verify return_native parameter
        original_search_tracks = mm_player.search_tracks
        search_calls = []

        def spy_search_tracks(*args, **kwargs):
            search_calls.append((args, kwargs))
            return original_search_tracks(*args, **kwargs)

        mm_player.search_tracks = spy_search_tracks

        playlist = Playlist(ID=100, name="Search Playlist")
        audio_track = AudioTag(ID="1", title="Search Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._add_track_to_playlist(playlist, audio_track)

        assert len(search_calls) == 1
        args, kwargs = search_calls[0]
        assert args == ("id", "1")
        assert kwargs.get("return_native") is True


class TestRemoveTrackFromPlaylist:
    def test_remove_success(self, mm_player, mm_playlist_factory, mm_track_factory):
        track = mm_track_factory(ID=1, Title="Remove Track")
        native_playlist = mm_playlist_factory(ID=100, Title="Remove Playlist", tracks=[track])
        mm_player.sdb.set_playlists([native_playlist])
        mm_player.sdb.set_tracks([track])

        playlist = Playlist(ID=100, name="Remove Playlist")
        audio_track = AudioTag(ID="1", title="Remove Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._remove_track_from_playlist(playlist, audio_track)

        assert len(native_playlist.RemoveTrack_calls) == 1
        assert native_playlist.RemoveTrack_calls[0].ID == 1

    def test_remove_playlist_not_found_logs_warning(self, mm_player, mm_track_factory):
        track = mm_track_factory(ID=1, Title="Test Track")
        mm_player.sdb.set_playlists([])
        mm_player.sdb.set_tracks([track])

        playlist = Playlist(ID=999, name="Missing Playlist")
        audio_track = AudioTag(ID="1", title="Test Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._remove_track_from_playlist(playlist, audio_track)

        mm_player.logger.warning.assert_called_with("Native playlist not found for Missing Playlist")

    def test_remove_track_not_found_logs_warning(self, mm_player, mm_playlist_factory):
        native_playlist = mm_playlist_factory(ID=100, Title="Test Playlist", tracks=[])
        mm_player.sdb.set_playlists([native_playlist])
        mm_player.sdb.set_tracks([])

        playlist = Playlist(ID=100, name="Test Playlist")
        audio_track = AudioTag(ID="999", title="Missing Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._remove_track_from_playlist(playlist, audio_track)

        mm_player.logger.warning.assert_called_with("Could not find track for: Artist - Album - Missing Track in MediaMonkey")

    def test_remove_uses_native_track_search(self, mm_player, mm_playlist_factory, mm_track_factory):
        track = mm_track_factory(ID=1, Title="Convert Track")
        native_playlist = mm_playlist_factory(ID=100, Title="Convert Playlist", tracks=[track])
        mm_player.sdb.set_playlists([native_playlist])
        mm_player.sdb.set_tracks([track])

        # Spy on search_tracks to verify return_native parameter
        original_search_tracks = mm_player.search_tracks
        search_calls = []

        def spy_search_tracks(*args, **kwargs):
            search_calls.append((args, kwargs))
            return original_search_tracks(*args, **kwargs)

        mm_player.search_tracks = spy_search_tracks

        playlist = Playlist(ID=100, name="Convert Playlist")
        audio_track = AudioTag(ID="1", title="Convert Track", artist="Artist", album="Album", track=1, rating=Rating(0.5, RatingScale.NORMALIZED))

        mm_player._remove_track_from_playlist(playlist, audio_track)

        assert len(search_calls) == 1
        args, kwargs = search_calls[0]
        assert args == ("id", "1")
        # Should have return_native=True, same as _add_track_to_playlist
        assert kwargs.get("return_native") is True


class TestPlaylistTracks:
    def test_read_returns_converted_tracks(self, mm_player, mm_playlist_factory, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Track One", ArtistName="Artist A", Rating=80)
        track2 = mm_track_factory(ID=2, Title="Track Two", ArtistName="Artist B", Rating=60)
        track3 = mm_track_factory(ID=3, Title="Track Three", ArtistName="Artist C", Rating=100)

        playlist = mm_playlist_factory(Title="Test Playlist", tracks=[track1, track2, track3])

        result = mm_player._read_native_playlist_tracks(playlist)

        assert isinstance(result, list)
        assert len(result) == 3

        for track in result:
            assert isinstance(track, AudioTag)

        assert result[0].title == "Track One"
        assert result[0].artist == "Artist A"
        assert result[0].rating.to_float(RatingScale.ZERO_TO_FIVE) == 4  # MediaMonkey 80 -> 5-star scale 4

        assert result[1].title == "Track Two"
        assert result[1].artist == "Artist B"
        assert result[1].rating.to_float(RatingScale.ZERO_TO_FIVE) == 3  # MediaMonkey 60 -> 5-star scale 3

        assert result[2].title == "Track Three"
        assert result[2].artist == "Artist C"
        assert result[2].rating.to_float(RatingScale.ZERO_TO_FIVE) == 5  # MediaMonkey 100 -> 5-star scale 5

    def test_read_handles_empty_playlist(self, mm_player, mm_playlist_factory):
        playlist = mm_playlist_factory(Title="Empty Playlist")

        result = mm_player._read_native_playlist_tracks(playlist)

        assert isinstance(result, list)
        assert len(result) == 0

    def test_read_iterates_by_count(self, mm_player, mm_playlist_factory, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Track One")
        track2 = mm_track_factory(ID=2, Title="Track Two")
        track3 = mm_track_factory(ID=3, Title="Track Three")

        playlist = mm_playlist_factory(Title="Test Playlist", tracks=[track1, track2])
        # Manually override tracks list to have 3 tracks but access only first 2
        playlist.Tracks._tracks = [track1, track2, track3]
        playlist.Tracks._tracks = playlist.Tracks._tracks[:2]  # Simulate Count = 2 behavior

        result = mm_player._read_native_playlist_tracks(playlist)

        assert len(result) == 2
        assert result[0].title == "Track One"
        assert result[1].title == "Track Two"

    def test_read_preserves_order(self, mm_player, mm_playlist_factory, mm_track_factory):
        track_z = mm_track_factory(ID=3, Title="Z Track", ArtistName="Z Artist")
        track_a = mm_track_factory(ID=1, Title="A Track", ArtistName="A Artist")
        track_m = mm_track_factory(ID=2, Title="M Track", ArtistName="M Artist")

        playlist = mm_playlist_factory(Title="Ordered Playlist", tracks=[track_z, track_a, track_m])

        result = mm_player._read_native_playlist_tracks(playlist)

        assert len(result) == 3
        assert result[0].title == "Z Track"  # First in playlist order
        assert result[1].title == "A Track"  # Second in playlist order
        assert result[2].title == "M Track"  # Third in playlist order

    @pytest.mark.parametrize(
        "count,description",
        [
            (0, "empty playlists"),
            (99, "normal playlists"),
            (101, "large playlists"),
        ],
    )
    def test_read_track_count(self, mm_player, mm_playlist_factory, mm_track_factory, count, description):
        dummy_tracks = [mm_track_factory(ID=i, Title=f"Track {i}") for i in range(count)]
        playlist = mm_playlist_factory(Title="Test Playlist", tracks=dummy_tracks)

        result = mm_player._get_native_playlist_track_count(playlist)

        assert result == count
        assert isinstance(result, int)

    def test_read_methods_integration(self, mm_player, mm_playlist_factory, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Integration Track 1")
        track2 = mm_track_factory(ID=2, Title="Integration Track 2")
        track3 = mm_track_factory(ID=3, Title="Integration Track 3")

        playlist = mm_playlist_factory(Title="Integration Playlist", tracks=[track1, track2, track3])

        # Get count and read tracks
        count = mm_player._get_native_playlist_track_count(playlist)
        tracks = mm_player._read_native_playlist_tracks(playlist)

        assert count == len(tracks)
        assert count == 3

        for track in tracks:
            assert isinstance(track, AudioTag)
            assert track.title.startswith("Integration Track")


class TestTrackSearch:
    def test_search_by_id_returns_track(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="A")
        track2 = mm_track_factory(ID=2, Title="B")
        mm_player.sdb.set_tracks([track1, track2])
        results = mm_player.search_tracks("id", 2)
        assert isinstance(results, list)
        assert any(getattr(t, "ID", None) == 2 for t in results)
        assert all(getattr(t, "ID", None) == 2 for t in results)

    def test_search_query_raises_exception(self, mm_player, mm_track_factory):
        mm_player.sdb.QuerySongs_raise = RuntimeError("Query failed")
        with pytest.raises(RuntimeError, match="Query failed"):
            mm_player.search_tracks("id", 1)

    def test_search_by_title_returns_matching(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="My Song")
        track2 = mm_track_factory(ID=2, Title="Another Song")
        track3 = mm_track_factory(ID=3, Title="My Song")  # Duplicate title
        mm_player.sdb.set_tracks([track1, track2, track3])

        results = mm_player.search_tracks("title", "My Song")
        assert isinstance(results, list)
        assert len(results) == 2
        assert all(getattr(t, "title", None) == "My Song" for t in results)

    def test_search_title_escapes_quotes(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title='Song with "quotes"')
        track2 = mm_track_factory(ID=2, Title="Regular Song")
        mm_player.sdb.set_tracks([track1, track2])

        results = mm_player.search_tracks("title", 'Song with "quotes"')
        assert isinstance(results, list)
        assert len(results) == 1
        assert getattr(results[0], "title", None) == 'Song with "quotes"'

    @pytest.mark.parametrize(
        "search_value,track_setup,expected_count,expected_ids",
        [
            # Test rating > 0 search
            (
                True,
                [
                    {"ID": 1, "Title": "Unrated", "Rating": 0},
                    {"ID": 2, "Title": "Rated Low", "Rating": 25},
                    {"ID": 3, "Title": "Rated High", "Rating": 85},
                ],
                2,
                {2, 3},
            ),
            (
                "= 50",
                [
                    {"ID": 1, "Title": "Low Rating", "Rating": 25},
                    {"ID": 2, "Title": "Medium Rating", "Rating": 50},
                    {"ID": 3, "Title": "High Rating", "Rating": 75},
                    {"ID": 4, "Title": "Also Medium", "Rating": 50},
                ],
                2,
                {2, 4},
            ),
        ],
        ids=["rating_greater_than_zero", "specific_rating_value"],
    )
    def test_search_by_rating_criteria(self, mm_player, mm_track_factory, search_value, track_setup, expected_count, expected_ids):
        tracks = [mm_track_factory(**track_data) for track_data in track_setup]
        mm_player.sdb.set_tracks(tracks)

        results = mm_player.search_tracks("rating", search_value)

        assert isinstance(results, list)
        assert len(results) == expected_count
        matched_ids = {getattr(t, "ID", None) for t in results}
        assert matched_ids == expected_ids

    def test_search_invalid_key_raises(self, mm_player):
        with pytest.raises(KeyError, match="Invalid search mode"):
            mm_player.search_tracks("invalid_key", "value")

    def test_search_empty_value_raises(self, mm_player):
        with pytest.raises(ValueError, match="value can not be empty"):
            mm_player.search_tracks("id", "")

    def test_search_return_native_bypasses_conversion(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Test Track")
        mm_player.sdb.set_tracks([track1])

        results = mm_player.search_tracks("id", 1, return_native=True)
        assert isinstance(results, list)
        assert len(results) == 1
        # Native track should have the original SimpleNamespace structure
        assert hasattr(results[0], "ID")
        assert hasattr(results[0], "Title")
        assert getattr(results[0], "ID", None) == 1
        assert getattr(results[0], "Title", None) == "Test Track"

    def test_search_by_query_passes_sql_directly(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Test Track", ArtistName="Test Artist", Rating=75)
        mm_player.sdb.set_tracks([track1])

        # Search using raw SQL query
        query_sql = "Artist='Test Artist' AND Rating > 50"
        results = mm_player.search_tracks("query", query_sql)

        assert isinstance(results, list)
        assert len(results) == 1
        assert getattr(results[0], "title", None) == "Test Track"

    def test_search_with_cache_returns_cached_result(self, mm_player, mm_track_factory):
        track1 = mm_track_factory(ID=123, Title="Cached Track")
        mm_player.sdb.set_tracks([track1])

        cached_tag = AudioTag(title="Cached", artist="Test", album="Album", file_path="/test")
        mm_player.cache_mgr.get_metadata.return_value = cached_tag

        # Search by ID should return cached result when return_native=False
        results = mm_player.search_tracks("id", 123, return_native=False)
        assert len(results) == 1
        assert results[0] == cached_tag
        mm_player.cache_mgr.get_metadata.assert_called_once_with("MediaMonkey", "123")

    def test_search_handles_large_resultsets(self, mm_player, mm_track_factory):
        tracks = [mm_track_factory(ID=i, Title="Test Track") for i in range(55)]
        mm_player.sdb.set_tracks(tracks)

        results = mm_player.search_tracks("title", "Test Track")

        assert isinstance(results, list)
        assert len(results) == 55
        assert all(hasattr(track, "title") for track in results)
        assert all(track.title == "Test Track" for track in results)


class TestTrackMetadata:
    @pytest.mark.parametrize(
        "cache_scenario,track_data,expected_cache_calls",
        [
            # Cache hit scenario
            (
                "hit",
                {"ID": 123, "Title": "Cached Track", "Rating": 75},
                {"get_called": True, "set_called": False, "returns_cached": True},
            ),
            # Cache miss scenario
            (
                "miss",
                {
                    "ID": 456,
                    "Title": "New Track",
                    "Rating": 50,
                    "ArtistName": "Test Artist",
                    "AlbumName": "Test Album",
                    "Path": "/path/to/song.mp3",
                    "TrackOrder": 3,
                    "SongLength": 240000,
                },
                {"get_called": True, "set_called": True, "returns_cached": False},
            ),
        ],
        ids=["cache_hit", "cache_miss"],
    )
    def test_metadata_with_cache_hit_and_miss(self, mm_player, mm_track_factory, cache_scenario, track_data, expected_cache_calls):
        native_track = mm_track_factory(**track_data)

        if cache_scenario == "hit":
            # Configure cache hit - return cached AudioTag
            cached_tag = AudioTag(ID=str(track_data["ID"]), title=track_data["Title"], artist="Cached Artist", album="Cached Album", track=1)
            mm_player.cache_mgr.get_metadata.return_value = cached_tag
        else:
            # Configure cache miss
            mm_player.cache_mgr.get_metadata.return_value = None

        result = mm_player._read_track_metadata(native_track)

        if expected_cache_calls["get_called"]:
            mm_player.cache_mgr.get_metadata.assert_called_once_with("MediaMonkey", track_data["ID"])

        if expected_cache_calls["set_called"]:
            mm_player.cache_mgr.set_metadata.assert_called_once_with("MediaMonkey", track_data["ID"], result)
        else:
            mm_player.cache_mgr.set_metadata.assert_not_called()

        if expected_cache_calls["returns_cached"]:
            assert result is mm_player.cache_mgr.get_metadata.return_value
        else:
            assert isinstance(result, AudioTag)
            assert result.ID == track_data["ID"]
            assert result.title == track_data["Title"]

    @pytest.mark.parametrize(
        "mm_rating,expected_normalized,is_unrated",
        [
            (85, 0.85, False),  # Valid rating
            (0, None, True),  # Unrated track
        ],
        ids=["valid_rating", "unrated_track"],
    )
    def test_metadata_rating_conversion(self, mm_player, mm_track_factory, mm_rating, expected_normalized, is_unrated):
        native_track = mm_track_factory(ID=789, Rating=mm_rating)
        mm_player.cache_mgr.get_metadata.return_value = None

        result = mm_player._read_track_metadata(native_track)

        assert isinstance(result.rating, Rating)
        if is_unrated:
            assert result.rating.is_unrated
        else:
            assert result.rating.to_float(RatingScale.NORMALIZED) == expected_normalized
            assert not result.rating.is_unrated

    def test_metadata_missing_song_length_defaults(self, mm_player, mm_track_factory):
        native_track = mm_track_factory(ID=111, SongLength=None)
        mm_player.cache_mgr.get_metadata.return_value = None

        result = mm_player._read_track_metadata(native_track)

        assert result.duration == -1

    def test_metadata_update_rating_success(self, mm_player, mm_track_factory):
        audio_tag = AudioTag(ID="555", title="Update Track", artist="Artist", album="Album", track=1)
        native_track = mm_track_factory(ID=555, Title="Update Track")

        # Add UpdateDB method to native track
        native_track.UpdateDB = MagicMock()

        # Configure search to return the native track
        mm_player.sdb.set_tracks([native_track])

        new_rating = Rating(0.9, scale=RatingScale.NORMALIZED)  # Should convert to 90/100
        mm_player._update_rating(audio_tag, new_rating)

        assert native_track.Rating == 90.0
        native_track.UpdateDB.assert_called_once()
        mm_player.logger.debug.assert_called_with("Updating rating for Artist - Album - Update Track to 4.5")

    @pytest.mark.parametrize(
        "error_scenario,track_id,track_title,setup_error,expected_exception,error_check",
        [
            # Track not found scenario
            (
                "track_not_found",
                "999",
                "Missing Track",
                "empty_tracks",
                IndexError,
                lambda error_msg: "Failed to update rating:" in error_msg,
            ),
            # UpdateDB failure scenario
            (
                "updatedb_failure",
                "777",
                "Failing Track",
                "updatedb_exception",
                RuntimeError,
                lambda error_msg: "Database update failed" in str(error_msg),
            ),
        ],
        ids=["track_not_found", "updatedb_failure"],
    )
    def test_metadata_update_rating_handles_errors(self, mm_player, mm_track_factory, error_scenario, track_id, track_title, setup_error, expected_exception, error_check):
        audio_tag = AudioTag(ID=track_id, title=track_title, artist="Artist", album="Album", track=1)
        new_rating = Rating(0.5, scale=RatingScale.NORMALIZED)

        if setup_error == "empty_tracks":
            # Configure search to return empty list (track not found)
            mm_player.sdb.set_tracks([])
        elif setup_error == "updatedb_exception":
            # Configure UpdateDB to raise an exception
            native_track = mm_track_factory(ID=int(track_id), Title=track_title)
            update_error = RuntimeError("Database update failed")
            native_track.UpdateDB = MagicMock(side_effect=update_error)
            mm_player.sdb.set_tracks([native_track])

        with pytest.raises(expected_exception):
            mm_player._update_rating(audio_tag, new_rating)

        mm_player.logger.error.assert_called()

        if setup_error == "empty_tracks":
            error_call_args = mm_player.logger.error.call_args[0][0]
            assert error_check(error_call_args)
        elif setup_error == "updatedb_exception":
            # For updatedb failure, also verify rating was set before failure
            assert native_track.Rating == 50.0  # 0.5 normalized -> 50 MediaMonkey scale
            native_track.UpdateDB.assert_called_once()

    def test_metadata_update_converts_scale(self, mm_player, mm_track_factory):
        audio_tag = AudioTag(ID="333", title="Scale Test", artist="Artist", album="Album", track=1)
        native_track = mm_track_factory(ID=333)
        native_track.UpdateDB = MagicMock()
        mm_player.sdb.set_tracks([native_track])

        test_cases = [
            (Rating(0.0, scale=RatingScale.NORMALIZED), 0.0),  # Unrated
            (Rating(0.5, scale=RatingScale.NORMALIZED), 50.0),  # Middle rating
            (Rating(1.0, scale=RatingScale.NORMALIZED), 100.0),  # Max rating
        ]

        for input_rating, expected_mm_rating in test_cases:
            # Reset the native track rating
            native_track.Rating = 0
            native_track.UpdateDB.reset_mock()

            mm_player._update_rating(audio_tag, input_rating)

            assert native_track.Rating == expected_mm_rating
            native_track.UpdateDB.assert_called_once()

    def test_metadata_integration_all_fields(self, mm_player, mm_track_factory):
        native_track = mm_track_factory(
            ID=12345,
            Title="Complete Track Info",
            Rating=75,
            ArtistName="Integration Artist",
            AlbumName="Integration Album",
            Path="/full/path/to/track.mp3",
            TrackOrder=7,
            SongLength=195000,  # 3:15 in milliseconds
        )
        mm_player.cache_mgr.get_metadata.return_value = None

        result = mm_player._read_track_metadata(native_track)

        assert result.ID == 12345
        assert result.title == "Complete Track Info"
        assert result.artist == "Integration Artist"
        assert result.album == "Integration Album"
        assert result.file_path == "/full/path/to/track.mp3"
        assert result.track == 7
        assert result.duration == 195  # Converted from ms to seconds
        assert result.rating.to_float(RatingScale.NORMALIZED) == 0.75  # 75/100 converted to normalized


class TestLoadPlaylistTracks:
    def test_load_successful_track_loading(self, mm_player, mm_playlist_factory, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Load Track 1", ArtistName="Artist A", Rating=60)
        track2 = mm_track_factory(ID=2, Title="Load Track 2", ArtistName="Artist B", Rating=80)
        native_playlist = mm_playlist_factory(ID=100, Title="Load Test Playlist", tracks=[track1, track2])
        mm_player.sdb.set_playlists([native_playlist])

        playlist = Playlist(ID=100, name="Load Test Playlist")
        playlist.is_auto_playlist = False
        playlist.tracks = []

        mm_player.load_playlist_tracks(playlist)

        assert len(playlist.tracks) == 2
        assert playlist.tracks[0].title == "Load Track 1"
        assert playlist.tracks[0].artist == "Artist A"
        assert playlist.tracks[1].title == "Load Track 2"
        assert playlist.tracks[1].artist == "Artist B"

    def test_load_missing_playlist_warning(self, mm_player):
        mm_player.sdb.set_playlists([])

        playlist = Playlist(ID=999, name="Missing Playlist")
        playlist.is_auto_playlist = False
        playlist.tracks = []

        mm_player.load_playlist_tracks(playlist)

        mm_player.logger.warning.assert_called_with("Playlist 'Missing Playlist' not found")
        assert len(playlist.tracks) == 0

    def test_load_auto_playlist_skips(self, mm_player, mm_playlist_factory, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Auto Track")
        native_playlist = mm_playlist_factory(ID=200, Title="Auto Playlist", isAutoplaylist=True, tracks=[track1])
        mm_player.sdb.set_playlists([native_playlist])

        playlist = Playlist(ID=200, name="Auto Playlist")
        playlist.is_auto_playlist = True
        playlist.tracks = []

        mm_player.load_playlist_tracks(playlist)

        assert len(playlist.tracks) == 0

    def test_load_empty_playlist_gracefully(self, mm_player, mm_playlist_factory):
        native_playlist = mm_playlist_factory(ID=300, Title="Empty Playlist", tracks=[])
        mm_player.sdb.set_playlists([native_playlist])

        playlist = Playlist(ID=300, name="Empty Playlist")
        playlist.is_auto_playlist = False
        playlist.tracks = []

        mm_player.load_playlist_tracks(playlist)

        assert len(playlist.tracks) == 0

    @pytest.mark.parametrize(
        "track_count,expect_progress_bar",
        [
            (99, False),  # Just under threshold
            (101, True),  # Over threshold - progress bar expected
        ],
        ids=["under_threshold", "over_threshold"],
    )
    def test_load_progress_bar_threshold(self, mm_player, mm_playlist_factory, mm_track_factory, track_count, expect_progress_bar):
        tracks = [mm_track_factory(ID=i, Title=f"Progress Track {i}") for i in range(1, track_count + 1)]
        native_playlist = mm_playlist_factory(ID=400, Title="Progress Test", tracks=tracks)
        mm_player.sdb.set_playlists([native_playlist])

        progress_bar_mock = MagicMock()
        mm_player.status_mgr.start_phase.return_value = progress_bar_mock

        # Create target playlist

        playlist = Playlist(ID=400, name="Progress Test")
        playlist.is_auto_playlist = False
        playlist.tracks = []

        mm_player.load_playlist_tracks(playlist)

        if expect_progress_bar:
            mm_player.status_mgr.start_phase.assert_called_once_with("Reading tracks from playlist Progress Test", total=track_count)
            assert progress_bar_mock.update.call_count == track_count
            progress_bar_mock.close.assert_called_once()
        else:
            mm_player.status_mgr.start_phase.assert_not_called()
            progress_bar_mock.update.assert_not_called()
            progress_bar_mock.close.assert_not_called()

        assert len(playlist.tracks) == track_count

    def test_load_metadata_conversion_error_handling(self, mm_player, mm_playlist_factory, mm_track_factory):
        track1 = mm_track_factory(ID=1, Title="Good Track")
        track2 = mm_track_factory(ID=2, Title="Bad Track")  # This will cause error
        native_playlist = mm_playlist_factory(ID=500, Title="Error Test", tracks=[track1, track2])
        mm_player.sdb.set_playlists([native_playlist])

        original_read_track_metadata = mm_player._read_track_metadata
        call_count = 0

        def mock_read_track_metadata(track):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # Second call raises exception
                raise RuntimeError("Metadata conversion failed")
            return original_read_track_metadata(track)

        mm_player._read_track_metadata = mock_read_track_metadata

        playlist = Playlist(ID=500, name="Error Test")
        playlist.is_auto_playlist = False
        playlist.tracks = []

        with pytest.raises(RuntimeError, match="Metadata conversion failed"):
            mm_player.load_playlist_tracks(playlist)

        assert len(playlist.tracks) == 1
        assert playlist.tracks[0].title == "Good Track"

    def test_load_search_by_title_not_id(self, mm_player, mm_playlist_factory, mm_track_factory):
        # Create playlist
        track1 = mm_track_factory(ID=1, Title="Title Search Track")
        native_playlist = mm_playlist_factory(ID=600, Title="Title Search Test", tracks=[track1])
        mm_player.sdb.set_playlists([native_playlist])

        # Create target playlist with different name but same ID

        playlist = Playlist(ID=600, name="Title Search Test")  # name matches playlist title
        playlist.is_auto_playlist = False
        playlist.tracks = []

        # Spy on search_playlists to verify search method
        original_search = mm_player.search_playlists
        search_calls = []

        def spy_search_playlists(*args, **kwargs):
            search_calls.append((args, kwargs))
            return original_search(*args, **kwargs)

        mm_player.search_playlists = spy_search_playlists

        mm_player.load_playlist_tracks(playlist)

        assert len(search_calls) == 1
        assert search_calls[0][0] == ("title", "Title Search Test")
        assert search_calls[0][1]["return_native"] is True

        # Verify track was loaded
        assert len(playlist.tracks) == 1
        assert playlist.tracks[0].title == "Title Search Track"

    def test_load_integration_with_metadata(self, mm_player, mm_playlist_factory, mm_track_factory):
        # Set up complex track data to verify metadata reading
        track1 = mm_track_factory(
            ID=1,
            Title="Integration Track",
            ArtistName="Integration Artist",
            AlbumName="Integration Album",
            Rating=75,
            TrackOrder=3,
            SongLength=210000,  # 3.5 minutes
            Path="/integration/path.mp3",
        )
        native_playlist = mm_playlist_factory(ID=700, Title="Integration Test", tracks=[track1])
        mm_player.sdb.set_playlists([native_playlist])

        # Create target playlist

        playlist = Playlist(ID=700, name="Integration Test")
        playlist.is_auto_playlist = False
        playlist.tracks = []

        mm_player.load_playlist_tracks(playlist)

        assert len(playlist.tracks) == 1
        loaded_track = playlist.tracks[0]

        assert loaded_track.title == "Integration Track"
        assert loaded_track.artist == "Integration Artist"
        assert loaded_track.album == "Integration Album"
        assert loaded_track.track == 3
        assert loaded_track.duration == 210  # Converted from milliseconds to seconds
        assert loaded_track.file_path == "/integration/path.mp3"

        assert loaded_track.rating.to_float(RatingScale.ZERO_TO_FIVE) == 3.75  # 75/100 * 5

    def test_load_debug_logging_with_progress(self, mm_player, mm_playlist_factory, mm_track_factory):
        # Create large playlist to trigger progress bar and debug logging
        tracks = [mm_track_factory(ID=i, Title=f"Debug Track {i}") for i in range(1, 151)]  # 150 tracks
        native_playlist = mm_playlist_factory(ID=800, Title="Debug Test", tracks=tracks)
        mm_player.sdb.set_playlists([native_playlist])

        # Mock status manager
        progress_bar_mock = MagicMock()
        mm_player.status_mgr.start_phase.return_value = progress_bar_mock

        # Create target playlist

        playlist = Playlist(ID=800, name="Debug Test")
        playlist.is_auto_playlist = False
        playlist.tracks = []

        mm_player.load_playlist_tracks(playlist)

        assert mm_player.logger.debug.call_count == 150

        # Verify debug message format
        debug_calls = mm_player.logger.debug.call_args_list
        assert "Reading track" in debug_calls[0][0][0]
        assert "from playlist Debug Test" in debug_calls[0][0][0]
