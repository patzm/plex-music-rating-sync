"""
Test suite for PlaylistPair in sync_pair.py
Covers: match, sync, _create_new_playlist, _update_existing_playlist, _match_tracks
"""

from unittest.mock import MagicMock, create_autospec

import pytest

from MediaPlayer import MediaPlayer
from sync_items import AudioTag, Playlist
from sync_pair import PlaylistPair, SyncState, TrackPair


def _make_pair(playlist_pair, src_artist, src_album, src_title, dst_artist, dst_album, dst_title):
    pair = TrackPair(playlist_pair.source_player, playlist_pair.destination_player, AudioTag(artist=src_artist, album=src_album, title=src_title))
    pair.destination = AudioTag(artist=dst_artist, album=dst_album, title=dst_title)
    return pair


@pytest.fixture
def source_playlist():
    pl = Playlist(ID="src1", name="Test Playlist")
    pl.tracks = [AudioTag(artist="A1", album="AL1", title="T1"), AudioTag(artist="A2", album="AL2", title="T2")]
    return pl


@pytest.fixture
def destination_playlist():
    pl = Playlist(ID="dst1", name="Test Playlist")
    pl.tracks = [AudioTag(artist="A1", album="AL1", title="T1"), AudioTag(artist="A2", album="AL2", title="T2")]
    return pl


@pytest.fixture
def source_player():
    mp = create_autospec(MediaPlayer)
    mp.load_playlist_tracks = MagicMock()
    mp.search_playlists = MagicMock()
    mp.name.return_value = "SourcePlayer"
    return mp


@pytest.fixture
def destination_player():
    mp = create_autospec(MediaPlayer)
    mp.load_playlist_tracks = MagicMock()
    mp.search_playlists = MagicMock()
    mp.create_playlist = MagicMock()
    mp.sync_playlist = MagicMock()
    mp.name.return_value = "DestinationPlayer"
    return mp


@pytest.fixture
def playlist_pair(source_player, destination_player, source_playlist):
    pair = PlaylistPair(source_player, destination_player, source_playlist)
    pair.logger = MagicMock()
    return pair


class TestPlaylistMatch:
    def test_match_needs_update_missing_tracks(self, playlist_pair, destination_player, source_playlist, destination_playlist):
        # Unique branch: Destination playlist found, tracks do NOT match source
        destination_player.search_playlists.return_value = [destination_playlist]
        destination_playlist.tracks = [AudioTag(artist="A1", album="AL1", title="T1")]
        source_playlist.tracks = [AudioTag(artist="A1", album="AL1", title="T1"), AudioTag(artist="A2", album="AL2", title="T2")]
        playlist_pair.stats_mgr = MagicMock()
        result = playlist_pair.match()
        assert result is True
        assert playlist_pair.sync_state == SyncState.NEEDS_UPDATE
        playlist_pair.stats_mgr.increment.assert_called_once_with("playlists_matched")

    def test_match_no_destination_playlist_returns_false(self, playlist_pair, destination_player, source_playlist):
        # Branch: No destination playlist found
        destination_player.search_playlists.return_value = []
        playlist_pair.stats_mgr = MagicMock()
        result = playlist_pair.match()
        assert result is False
        assert playlist_pair.sync_state == SyncState.NEEDS_UPDATE
        playlist_pair.logger.info.assert_called_once()
        playlist_pair.stats_mgr.increment.assert_not_called()

    def test_match_source_tracks_empty_loads_tracks(self, playlist_pair, destination_player, source_playlist, destination_playlist, source_player):
        # Branch: Source playlist has no tracks, triggers load
        destination_player.search_playlists.return_value = [destination_playlist]
        source_playlist.tracks = []
        playlist_pair.stats_mgr = MagicMock()
        playlist_pair.match()
        source_player.load_playlist_tracks.assert_called_once_with(source_playlist)

    def test_match_destination_tracks_empty_loads_tracks(self, playlist_pair, destination_player, source_playlist, destination_playlist):
        # Branch: Destination playlist has no tracks, triggers load
        destination_player.search_playlists.return_value = [destination_playlist]
        destination_playlist.tracks = []
        playlist_pair.stats_mgr = MagicMock()
        playlist_pair.match()
        destination_player.load_playlist_tracks.assert_called_once_with(destination_playlist)

    def test_match_no_missing_tracks_up_to_date(self, playlist_pair, destination_player, source_playlist, destination_playlist):
        # Branch: No missing tracks, playlist is up to date
        destination_player.search_playlists.return_value = [destination_playlist]
        destination_playlist.tracks = [AudioTag(artist="A1", album="AL1", title="T1")]
        source_playlist.tracks = [AudioTag(artist="A1", album="AL1", title="T1")]
        destination_playlist.missing_tracks = MagicMock(return_value=[])
        playlist_pair.stats_mgr = MagicMock()
        result = playlist_pair.match()
        assert result is True
        assert playlist_pair.sync_state == SyncState.UP_TO_DATE
        playlist_pair.stats_mgr.increment.assert_called_once_with("playlists_matched")


class TestTrackMatch:
    def test_match_tracks_all_matched(self, playlist_pair, destination_player):
        track = AudioTag(artist="A1", album="AL1", title="T1")
        playlist_pair.source.tracks = [track]
        playlist_pair.destination = Playlist(ID="dst1", name="Test Playlist")
        playlist_pair.destination.tracks = [track]
        destination_player.search_tracks.return_value = [track]
        pairs, unmatched = playlist_pair._match_tracks()
        assert len(pairs) == 1
        assert len(unmatched) == 0

    def test_match_tracks_some_unmatched(self, playlist_pair, destination_player):
        track1 = AudioTag(artist="A1", album="AL1", title="T1")
        track2 = AudioTag(artist="A2", album="AL2", title="T2")
        playlist_pair.source.tracks = [track1, track2]
        playlist_pair.destination = Playlist(ID="dst1", name="Test Playlist")
        playlist_pair.destination.tracks = [track1]
        # Only track1 is a candidate for matching
        destination_player.search_tracks.side_effect = lambda **kwargs: [track1] if kwargs.get("value") == "T1" else []
        pairs, unmatched = playlist_pair._match_tracks()
        assert len(pairs) == 1
        assert len(unmatched) == 1
        assert unmatched[0] == track2

    def test_match_tracks_empty_source(self, playlist_pair):
        playlist_pair.source.tracks = []
        pairs, unmatched = playlist_pair._match_tracks()
        assert pairs == []
        assert unmatched == []

    def test_match_tracks_progress_bar(self, playlist_pair, destination_player):
        playlist_pair.source.tracks = [AudioTag(artist=f"A{i}", album=f"AL{i}", title=f"T{i}") for i in range(51)]
        playlist_pair.destination = Playlist(ID="dst1", name="Test Playlist")
        playlist_pair.destination.tracks = [AudioTag(artist=f"A{i}", album=f"AL{i}", title=f"T{i}") for i in range(51)]
        destination_player.search_tracks.side_effect = lambda **kwargs: [
            AudioTag(artist=kwargs.get("value").replace("T", "A"), album=kwargs.get("value").replace("T", "AL"), title=kwargs.get("value"))
        ]
        playlist_pair.status_mgr = MagicMock()
        pairs, unmatched = playlist_pair._match_tracks()
        assert len(pairs) == 51
        assert len(unmatched) == 0
        playlist_pair.status_mgr.start_phase.assert_called_once()


class TestPlaylistCreate:
    def test_create_new_playlist(self, playlist_pair, destination_player, source_playlist):
        playlist_pair.destination_player = destination_player
        playlist_pair.source = source_playlist
        track_pairs = [TrackPair(playlist_pair.source_player, playlist_pair.destination_player, AudioTag(artist="A1", album="AL1", title="T1"))]
        track_pairs[0].destination = AudioTag(artist="A1", album="AL1", title="T1")
        playlist_pair._create_new_playlist(track_pairs)
        destination_player.create_playlist.assert_called_once_with(source_playlist.name, [pair.destination for pair in track_pairs])


class TestPlaylistUpdate:
    @pytest.mark.parametrize(
        "track_pairs, has_track_side_effects, expected_result, expect_update",
        [
            ([lambda pp: _make_pair(pp, "A1", "AL1", "T1", "A1", "AL1", "T1")], [True], False, False),
            ([], [], False, False),
            (
                [lambda pp: _make_pair(pp, "A1", "AL1", "T1", "A2", "AL2", "T2"), lambda pp: _make_pair(pp, "A2", "AL2", "T2", "A2", "AL2", "T2")],
                [False, True],
                True,
                True,
            ),
        ],
    )
    def test_update_existing_playlist(self, playlist_pair, destination_player, destination_playlist, track_pairs, has_track_side_effects, expected_result, expect_update):
        playlist_pair.destination = destination_playlist
        playlist_pair.destination_player = destination_player
        real_track_pairs = [fn(playlist_pair) for fn in track_pairs]
        if real_track_pairs:

            def has_track_side_effect(track):
                return has_track_side_effects.pop(0)

            destination_playlist.has_track = has_track_side_effect
        destination_player.sync_playlist.reset_mock()
        result = playlist_pair._update_existing_playlist(real_track_pairs)
        assert result is expected_result
        if expect_update:
            destination_player.sync_playlist.assert_called_once()
        else:
            destination_player.sync_playlist.assert_not_called()


class TestPlaylistSync:
    def test_sync_up_to_date(self, playlist_pair):
        playlist_pair.sync_state = SyncState.UP_TO_DATE
        assert playlist_pair.sync() is True

    def test_sync_creates_new_playlist(self, playlist_pair, destination_player):
        playlist_pair.sync_state = SyncState.NEEDS_UPDATE
        playlist_pair.destination = None
        playlist_pair.source.tracks = [AudioTag(artist="A1", album="AL1", title="T1")]

        def fake_match_tracks():
            pair = TrackPair(playlist_pair.source_player, playlist_pair.destination_player, AudioTag(artist="A1", album="AL1", title="T1"))
            pair.destination = AudioTag(artist="A1", album="AL1", title="T1")
            return ([pair], [])

        playlist_pair._match_tracks = fake_match_tracks
        playlist_pair._create_new_playlist = lambda pairs: True
        assert playlist_pair.sync() is True

    def test_sync_create_new_playlist_failure(self, playlist_pair, destination_player):
        playlist_pair.sync_state = SyncState.NEEDS_UPDATE
        playlist_pair.destination = None
        playlist_pair.source.tracks = [AudioTag(artist="A1", album="AL1", title="T1")]

        def fake_match_tracks():
            pair = TrackPair(playlist_pair.source_player, playlist_pair.destination_player, AudioTag(artist="A1", album="AL1", title="T1"))
            pair.destination = AudioTag(artist="A1", album="AL1", title="T1")
            return ([pair], [])

        playlist_pair._match_tracks = fake_match_tracks
        playlist_pair._create_new_playlist = lambda pairs: False
        assert playlist_pair.sync() is False

    def test_sync_updates_existing_playlist(self, playlist_pair, destination_player, destination_playlist):
        playlist_pair.sync_state = SyncState.NEEDS_UPDATE
        playlist_pair.destination = destination_playlist
        playlist_pair.source.tracks = [AudioTag(artist="A1", album="AL1", title="T1")]

        def fake_match_tracks():
            pair = TrackPair(playlist_pair.source_player, playlist_pair.destination_player, AudioTag(artist="A1", album="AL1", title="T1"))
            pair.destination = AudioTag(artist="A1", album="AL1", title="T1")
            return ([pair], [])

        playlist_pair._match_tracks = fake_match_tracks
        playlist_pair._update_existing_playlist = lambda pairs: True
        assert playlist_pair.sync() is True

    def test_sync_with_unmatched_tracks_logs_warning(self, playlist_pair):
        playlist_pair.sync_state = None
        playlist_pair.logger = MagicMock()
        unmatched = [AudioTag(artist="A1", album="AL1", title="T1"), AudioTag(artist="A2", album="AL2", title="T2")]
        playlist_pair._match_tracks = MagicMock(return_value=([MagicMock()], unmatched))
        playlist_pair.sync()
        playlist_pair.logger.warning.assert_any_call(f"Failed to match {len(unmatched)} tracks:")
        for track in unmatched:
            playlist_pair.logger.warning.assert_any_call(f"  - {track}")

    def test_sync_no_track_pairs_returns_false(self, playlist_pair):
        playlist_pair.sync_state = None
        playlist_pair.logger = MagicMock()
        playlist_pair._match_tracks = MagicMock(return_value=([], []))
        result = playlist_pair.sync()
        playlist_pair.logger.warning.assert_called_with(f"No tracks could be matched for playlist {playlist_pair.source.name}")
        assert result is False
