from unittest.mock import MagicMock

import pytest

from MediaPlayer import MediaPlayer
from ratings import Rating, RatingScale
from sync_items import AudioTag, Playlist


class DummyMediaPlayer(MediaPlayer):
    @staticmethod
    def name() -> str:
        return "Dummy"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._add_track_called = False
        self._remove_track_called = False
        self._create_playlist_called = False
        self._update_rating_called = False

    def connect(self, *args, **kwargs):
        pass

    def _read_native_playlist_tracks(self, native_playlist):
        return getattr(native_playlist, "tracks", [])

    def _get_native_playlist_track_count(self, native_playlist):
        return len(getattr(native_playlist, "tracks", []))

    def _read_track_metadata(self, track):
        return track

    def _create_playlist(self, title, tracks):
        self._create_playlist_called = True
        return Playlist(ID="pl1", name=title)

    def _search_playlists(self, key, value=None, return_native=False):
        if key not in {"all", "title", "id"}:
            raise ValueError(f"Invalid search key: {key}")
        if key == "id" and value == "missing":
            return []
        dummy = MagicMock()
        dummy.ID = "pl1"
        dummy.name = "Playlist"
        dummy.tracks = []
        return [dummy] if return_native else [Playlist(ID="pl1", name="Playlist")]

    def _search_tracks(self, key, value, return_native=False):
        if not value:
            raise ValueError("value can not be empty.")
        return [AudioTag(ID="t1", title="Title", artist="Artist", album="Album", track=1, rating=Rating(1, scale=RatingScale.NORMALIZED))]

    def _add_track_to_playlist(self, playlist, track):
        self._add_track_called = True
        playlist.tracks.append(track)

    def _remove_track_from_playlist(self, playlist, track):
        self._remove_track_called = True
        if track in playlist.tracks:
            playlist.tracks.remove(track)

    def _update_rating(self, track, rating):
        self._update_rating_called = True
        track.rating = rating


class FailingDummyMediaPlayer(DummyMediaPlayer):
    def _create_playlist(self, title, tracks):
        return None

    def _add_track_to_playlist(self, playlist, track):
        raise RuntimeError("fail")

    def _remove_track_from_playlist(self, playlist, track):
        raise RuntimeError("fail")

    def _update_rating(self, track, rating):
        raise RuntimeError("fail")


@pytest.fixture(scope="function")
def dummy_player():
    player = DummyMediaPlayer()
    player.logger = MagicMock()
    return player


class TestMediaPlayer:
    @pytest.mark.parametrize(
        "album,alias,expected",
        [
            ("", "", True),
            ("foo", "", False),
            (None, "", False),
        ],
    )
    def test_album_empty_result(self, dummy_player, album, alias, expected):
        dummy_player.album_empty_alias = alias
        assert dummy_player.album_empty(album) is expected

    def test_str_hash_eq_behavior(self, dummy_player):
        p2 = DummyMediaPlayer()
        assert str(dummy_player) == "Dummy"
        assert hash(dummy_player) == hash("dummy")
        assert dummy_player == p2
        assert not (dummy_player == object())


class TestPlaylistSearch:
    @pytest.mark.parametrize(
        "key,expect_error",
        [
            ("all", False),
            ("title", False),
            ("id", False),
            ("badkey", True),
        ],
    )
    def test_search_playlists_key_behavior(self, dummy_player, key, expect_error):
        if expect_error:
            with pytest.raises(ValueError):
                dummy_player.search_playlists(key)
        else:
            result = dummy_player.search_playlists(key)
            assert isinstance(result, list)

    @pytest.mark.parametrize(
        "playlist_id,is_auto_playlist,total_tracks,track_count,expect_bar,expect_warning,expect_tracks",
        [
            ("missing", False, 0, 0, False, True, []),
            ("pl1", True, 150, 2, False, False, []),  # auto playlist: tracks not loaded in DummyMediaPlayer
            ("pl1", False, 50, 2, False, False, ["track0", "track1"]),
            ("pl1", False, 150, 0, True, False, []),
            ("pl1", False, 150, 2, True, False, ["track0", "track1"]),
        ],
    )
    def test_load_playlist_tracks_scenarios(self, dummy_player, playlist_id, is_auto_playlist, total_tracks, track_count, expect_bar, expect_warning, expect_tracks):
        pl = Playlist(ID=playlist_id, name="Playlist")
        pl.is_auto_playlist = is_auto_playlist
        pl.tracks = []
        bar_mock = MagicMock()
        dummy_player.status_mgr = MagicMock()
        dummy_player.status_mgr.start_phase.return_value = bar_mock
        dummy_player._get_native_playlist_track_count = MagicMock(return_value=total_tracks)
        dummy_player._read_native_playlist_tracks = MagicMock(return_value=[f"track{i}" for i in range(track_count)])
        dummy_player._read_track_metadata = lambda t: t
        dummy_player.logger = MagicMock()
        dummy_player.load_playlist_tracks(pl)
        assert pl.tracks == expect_tracks
        if expect_warning:
            dummy_player.logger.warning.assert_called()
        if expect_bar:
            dummy_player.status_mgr.start_phase.assert_called_once()
            # Accept any call to bar_mock.update, since DummyMediaPlayer doesn't call with arguments
            assert bar_mock.update.call_count == track_count
            bar_mock.close.assert_called_once()
            # Accept any call to logger.debug, since DummyMediaPlayer doesn't call with arguments
            assert dummy_player.logger.debug.call_count == track_count
        else:
            dummy_player.status_mgr.start_phase.assert_not_called()
            bar_mock.update.assert_not_called()
            bar_mock.close.assert_not_called()


class TestPlaylistTitleRegistration:
    @pytest.mark.parametrize(
        "registrations,expect_error,expected_map",
        [
            ([("foo", "id1"), ("foo", "id1")], None, {"foo": "id1", "id1": "foo"}),
            ([("foo", "id1"), ("foo", "id2")], ValueError, {"foo": "id1", "id1": "foo"}),
            ([("foo", "id1"), ("bar", "id1")], None, {"foo": "id1", "bar": "id1", "id1": "bar"}),
            ([("foo", "id1"), ("bar", "id2")], None, {"foo": "id1", "id1": "foo", "bar": "id2", "id2": "bar"}),
        ],
        ids=[
            "register_same_title_same_id_idempotent",
            "register_same_title_different_id_raises",
            "register_new_title_for_existing_id_updates_mapping",
            "register_two_different_titles_and_ids",
        ],
    )
    def test_register_playlist_title_scenarios(self, dummy_player, registrations, expect_error, expected_map):
        """Parameterized test for playlist title registration"""
        last_successful_map = dict(dummy_player._title_to_id_map)
        error_raised = None
        for title, pid in registrations:
            try:
                dummy_player._register_playlist_title(title, pid)
                last_successful_map = dict(dummy_player._title_to_id_map)
            except Exception as e:
                error_raised = e
                if expect_error:
                    assert isinstance(e, expect_error)
                    # After error, mapping should be unchanged from last successful registration
                    assert dummy_player._title_to_id_map == last_successful_map
                    break
                else:
                    raise
        if not expect_error:
            # Assert all expected mappings
            for k, v in expected_map.items():
                assert dummy_player._title_to_id_map[k] == v
        else:
            assert error_raised is not None


class TestPlaylistUpdate:
    @pytest.mark.parametrize(
        "updates,dry_run,expect_add,expect_remove,expect_log,expect_error",
        [
            ([], False, False, False, "No updates to sync", False),
            ([(AudioTag(ID="t1", title="", artist="", album="", track=1, rating=Rating(1, scale=RatingScale.NORMALIZED)), True)], True, False, False, None, False),
            (
                [(AudioTag(ID="t1", title="", artist="", album="", track=1, rating=Rating(1, scale=RatingScale.NORMALIZED)), True)],
                False,
                True,
                False,
                "Syncing 1 changes to playlist 'Playlist'",
                False,
            ),
            (object(), False, False, False, None, True),
        ],
    )
    def test_sync_playlist_update_behavior(self, dummy_player, updates, dry_run, expect_add, expect_remove, expect_log, expect_error):
        pl = Playlist(ID="pl1", name="Playlist")
        dummy_player.dry_run = dry_run
        dummy_player.logger = MagicMock()
        original_add = dummy_player._add_track_called
        original_remove = dummy_player._remove_track_called
        if expect_error:
            with pytest.raises(TypeError):
                dummy_player.sync_playlist(pl, updates)
            assert dummy_player._add_track_called == original_add
            assert dummy_player._remove_track_called == original_remove
        else:
            dummy_player.sync_playlist(pl, updates)
            if expect_log:
                if expect_log == "No updates to sync":
                    dummy_player.logger.debug.assert_any_call(expect_log)  # Corrected: check debug, not info
                else:
                    dummy_player.logger.info.assert_any_call(expect_log)
            if expect_add:
                assert dummy_player._add_track_called
            if expect_remove:
                assert dummy_player._remove_track_called
        dummy_player.dry_run = False

    @pytest.mark.parametrize(
        "dry_run,tracks,fail,expect_result,expect_log,expect_error",
        [
            (True, [AudioTag(ID="t1", title="", artist="", album="", track=1, rating=Rating(1, scale=RatingScale.NORMALIZED))], False, None, None, False),
            (False, [], False, None, None, False),
            (
                False,
                [AudioTag(ID="t1", title="", artist="", album="", track=1, rating=Rating(1, scale=RatingScale.NORMALIZED))],
                False,
                Playlist,
                "Successfully created playlist 'foo'",
                False,
            ),
            (
                False,
                [AudioTag(ID="t1", title="", artist="", album="", track=1, rating=Rating(1, scale=RatingScale.NORMALIZED))],
                True,
                None,
                "Failed to create playlist 'foo'",
                False,
            ),
        ],
    )
    def test_create_playlist_outcome(self, dummy_player, dry_run, tracks, fail, expect_result, expect_log, expect_error):
        if fail:
            player = FailingDummyMediaPlayer()
            player.logger = MagicMock()
        else:
            player = dummy_player
        player.dry_run = dry_run
        player.logger = MagicMock()
        result = player.create_playlist("foo", tracks)
        if expect_result:
            assert isinstance(result, expect_result)
            player.logger.debug.assert_any_call(expect_log)
        elif expect_log:
            player.logger.error.assert_any_call(expect_log) if fail else player.logger.info.assert_called()
        else:
            assert result is None
            if not dry_run:
                player.logger.warning.assert_called()
        player.dry_run = False

    @pytest.mark.parametrize(
        "dry_run,present,fail,expect_error,expect_log",
        [
            (True, True, False, False, None),
            (True, False, False, False, None),
            (False, True, False, False, "Successfully added track"),
            (False, False, False, False, "Successfully removed track"),
            (False, True, True, True, "Failed to add track: fail"),
            (False, False, True, True, "Failed to remove track: fail"),
        ],
    )
    def test_update_playlist_add_remove_behavior(self, dry_run, present, fail, expect_error, expect_log):
        if fail:
            player = FailingDummyMediaPlayer()
            player.logger = MagicMock()
        else:
            player = DummyMediaPlayer()
            player.logger = MagicMock()
        pl = Playlist(ID="pl1", name="Playlist")
        track = AudioTag(ID="t1", title="", artist="", album="", track=1, rating=Rating(1, scale=RatingScale.NORMALIZED))
        player.dry_run = dry_run
        if expect_error:
            with pytest.raises(RuntimeError):
                player.update_playlist(pl, track, present)
            player.logger.error.assert_any_call(expect_log)
        else:
            player.update_playlist(pl, track, present)
            if expect_log:
                player.logger.debug.assert_any_call(expect_log)
            else:
                player.logger.info.assert_called()
        player.dry_run = False


class TestTrackSearch:
    @pytest.mark.parametrize(
        "value,expect_error",
        [
            ("t1", False),
            ("", True),
        ],
    )
    def test_search_tracks_value_behavior(self, dummy_player, value, expect_error):
        if expect_error:
            with pytest.raises(ValueError):
                dummy_player.search_tracks("id", value)
        else:
            result = dummy_player.search_tracks("id", value)
            assert isinstance(result, list)
            assert result[0].ID == "t1"


class TestTrackUpdate:
    @pytest.mark.parametrize(
        "dry_run,fail,expect_error",
        [
            (True, False, False),
            (False, False, False),
            (False, True, True),
        ],
    )
    def test_update_rating_outcome(self, dry_run, fail, expect_error):
        if fail:
            player = FailingDummyMediaPlayer()
            player.logger = MagicMock()
        else:
            player = DummyMediaPlayer()
            player.logger = MagicMock()
        track = AudioTag(ID="t1", title="", artist="", album="", track=1, rating=Rating(0.5, scale=RatingScale.NORMALIZED))
        player.dry_run = dry_run
        if expect_error:
            with pytest.raises(RuntimeError):
                player.update_rating(track, Rating(1, scale=RatingScale.NORMALIZED))
            assert not player._update_rating_called
            player.logger.error.assert_any_call("Failed to update rating: fail")
            player.logger.info.assert_not_called()
        else:
            player.update_rating(track, Rating(1, scale=RatingScale.NORMALIZED))
            if dry_run:
                assert not player._update_rating_called
                player.logger.info.assert_any_call(f"DRY RUN: Would update rating for {track} to {Rating(1, scale=RatingScale.NORMALIZED).to_display()}")
            else:
                assert player._update_rating_called
                player.logger.info.assert_any_call(f"Successfully updated rating for {track}")
        player.dry_run = False
