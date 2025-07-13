from unittest.mock import MagicMock, patch

import pytest

from manager.config_manager import SyncItem
from ratings import Rating
from sync_items import AudioTag, Playlist
from sync_pair import MatchThreshold, PlaylistPair, SyncState, TrackPair

PROMPT_RESPONSES = []


@pytest.fixture
def plexsync(monkeypatch):
    """
    Provides a fully-initialized PlexSync instance with all external dependencies mocked.
    All mocks are attached as attributes for test-side configuration.
    Adds DummyPrompt to patch all user prompts to pop from prompt_return list.
    """
    # Mock player classes
    source_player = MagicMock(name="SourcePlayer")
    source_player.name.return_value = source_player.__str__.return_value = "SourcePlayer"
    dest_player = MagicMock(name="DestPlayer")
    dest_player.name.return_value = dest_player.__str__.return_value = "DestPlayer"
    monkeypatch.setattr("sync_ratings.Plex", lambda *a, **kw: source_player)
    monkeypatch.setattr("sync_ratings.MediaMonkey", lambda *a, **kw: dest_player)

    # # Mock logger
    mock_logger = MagicMock()
    monkeypatch.setattr("sync_ratings.logging.getLogger", lambda *a, **kw: mock_logger)

    class DummyPrompt:
        def choice(self, prompt, options, **kwargs):
            return PROMPT_RESPONSES.pop(0)

        def yes_no(self, *a, **k):
            return PROMPT_RESPONSES.pop(0) if PROMPT_RESPONSES else None

        def text(self, *a, **k):
            return PROMPT_RESPONSES.pop(0) if PROMPT_RESPONSES else None

        def confirm_continue(self, *a, **k):
            return PROMPT_RESPONSES.pop(0) if PROMPT_RESPONSES else None

    from sync_ratings import PlexSync

    instance = PlexSync()
    instance.source_player = source_player
    instance.dest_player = dest_player

    instance.prompt = DummyPrompt()
    return instance


@pytest.fixture
def audio_tag_factory():
    def _factory(**kwargs):
        if "rating" not in kwargs:
            kwargs["rating"] = Rating(0.9)
        return AudioTag(**kwargs)

    return _factory


@pytest.fixture
def trackpair_factory(audio_tag_factory):
    """Factory for creating real TrackPair objects with mocked players and real AudioTags.
    The .match() method is patched to avoid real matching logic and just set attributes for test needs.
    """

    def _factory(
        source_track=None, destination_track=None, source_player=None, destination_player=None, sync_state=SyncState.UP_TO_DATE, score=100, quality=MatchThreshold.PERFECT_MATCH
    ):
        if source_track is None:
            source_track = audio_tag_factory(title="Source", artist="Artist")
        if destination_track is None:
            destination_track = audio_tag_factory(title="Dest", artist="Artist")
        if source_player is None:
            source_player = MagicMock(name="SourcePlayer")
            source_player.name.return_value = "SourcePlayer"
        if destination_player is None:
            destination_player = MagicMock(name="DestPlayer")
            destination_player.name.return_value = "DestPlayer"
        pair = TrackPair(source_player, destination_player, source_track)
        pair.destination = destination_track
        pair.sync_state = sync_state
        pair.score = score
        pair.rating_source = getattr(source_track, "rating", None)
        pair.rating_destination = getattr(destination_track, "rating", None)

        def fake_match(*args, **kwargs):
            return pair

        pair.match = fake_match
        return pair

    return _factory


@pytest.fixture
def playlistpair_factory(plexsync, playlist_factory):
    """Factory for creating real PlaylistPair objects with test-controlled attributes."""

    def _factory(source_playlist=None, destination_playlist=None, source_player=None, destination_player=None, sync_state=None):
        if source_player is None:
            source_player = plexsync.source_player
        if destination_player is None:
            destination_player = plexsync.destination_player
        if source_playlist is None:
            source_playlist = playlist_factory(ID="pl1", name="Source Playlist", tracks=[])
        pair = PlaylistPair(source_player, destination_player, source_playlist)
        pair.logger = MagicMock()
        pair.status_mgr = MagicMock()
        pair.stats_mgr = MagicMock()
        if destination_playlist is not None:
            pair.destination = destination_playlist
        if sync_state is not None:
            pair.sync_state = sync_state
        return pair

    return _factory


@pytest.fixture
def playlist_factory():
    def _factory(ID="pl1", name="Test Playlist", tracks=None, is_auto_playlist=False):
        pl = Playlist(ID=ID, name=name)
        pl.is_auto_playlist = is_auto_playlist
        if tracks is not None:
            pl.tracks = tracks
        return pl

    return _factory


@pytest.fixture
def trackpairs(trackpair_factory):
    return [
        trackpair_factory(sync_state=SyncState.UP_TO_DATE, score=100),
        trackpair_factory(sync_state=SyncState.UP_TO_DATE, score=80),
        trackpair_factory(sync_state=SyncState.UP_TO_DATE, score=30),
        trackpair_factory(sync_state=SyncState.NEEDS_UPDATE, score=100),
        trackpair_factory(sync_state=SyncState.NEEDS_UPDATE, score=99),
        trackpair_factory(sync_state=SyncState.NEEDS_UPDATE, score=79),
        trackpair_factory(sync_state=SyncState.CONFLICTING, score=100),
        trackpair_factory(sync_state=SyncState.CONFLICTING, score=80),
        trackpair_factory(sync_state=SyncState.CONFLICTING, score=79),
        trackpair_factory(sync_state=SyncState.UNKNOWN, score=None),
        trackpair_factory(sync_state=SyncState.ERROR, score=None),
    ]


class TestSync:
    @pytest.mark.parametrize(
        "sync_items,tracks_calls,playlists_calls",
        [
            ([SyncItem.TRACKS, SyncItem.PLAYLISTS], 1, 1),
            ([SyncItem.TRACKS], 1, 0),
            ([SyncItem.PLAYLISTS], 0, 1),
        ],
    )
    def test_sync_routing(self, plexsync, sync_items, tracks_calls, playlists_calls):
        """Test that sync() routes to sync_tracks and/or sync_playlists as configured."""
        plexsync.config_mgr.sync = sync_items
        plexsync.sync_tracks = MagicMock()
        plexsync.sync_playlists = MagicMock()
        plexsync.sync()
        assert plexsync.sync_tracks.call_count == tracks_calls
        assert plexsync.sync_playlists.call_count == playlists_calls

    def test_sync_invalid_item_raises(self, plexsync, monkeypatch):
        """Test that sync() raises appropriate error for invalid sync items."""
        orig_sync = plexsync.config_mgr.sync
        plexsync.config_mgr.sync = ["invalid"]
        plexsync.sync_tracks = MagicMock()
        plexsync.sync_playlists = MagicMock()
        with pytest.raises(ValueError):
            plexsync.sync()
        plexsync.config_mgr.sync = orig_sync

    def test_sync_tracks_no_tracks_warns(self, plexsync):
        plexsync.source_player.search_tracks.return_value = []
        plexsync.sync_tracks()
        plexsync.logger.warning.assert_called_once_with("No tracks found")

    def test_sync_tracks_all_up_to_date_logs(self, plexsync, track_factory, trackpair_factory):
        track = track_factory()
        pair = trackpair_factory(source_track=track, sync_state=SyncState.UP_TO_DATE)
        plexsync.source_player.search_tracks.return_value = [track]
        plexsync.sync_pairs = []
        plexsync._match_tracks = MagicMock(return_value=[pair])
        plexsync.sync_tracks()
        plexsync.logger.info.assert_any_call("Attempting to match 1 tracks")

    def test_init_invalid_player_exits(self):
        """Test PlexSync initialization calls exit(1) when config has invalid player type."""
        from sync_ratings import get_manager

        manager = get_manager()
        config_mgr = manager.get_config_manager()
        orig_source = config_mgr.source
        config_mgr.source = "INVALID_PLAYER_TYPE"

        with pytest.raises(SystemExit) as exc_info:
            from sync_ratings import PlexSync

            PlexSync()
        assert exc_info.value.code == 1
        config_mgr.source = orig_source


class TestTrackSync:
    @pytest.fixture(autouse=True)
    def patch_trackpair_match(self, request):
        """Class-scoped fixture to patch TrackPair.match to set attributes from the source track for test control."""

        def fake_match(self, *args, **kwargs):
            src = self.source
            if hasattr(src, "_pair_score"):
                self.score = src._pair_score
            if hasattr(src, "_pair_state"):
                self.sync_state = src._pair_state
            if hasattr(src, "_pair_quality"):
                self._quality = src._pair_quality
            return self

        patcher = patch.object(TrackPair, "match", fake_match)
        patcher.start()
        request.addfinalizer(patcher.stop)

    def test_sync_tracks_user_cancel_no_sync(self, plexsync, track_factory, trackpair_factory):
        track = track_factory()
        pair = trackpair_factory(source_track=track, sync_state=SyncState.NEEDS_UPDATE)
        plexsync.source_player.search_tracks.return_value = [track]
        plexsync._match_tracks = MagicMock(return_value=[pair])
        plexsync._prompt_user_action = MagicMock(return_value=None)
        plexsync._sync_ratings = MagicMock()
        plexsync.sync_tracks()
        plexsync._sync_ratings.assert_not_called()

    def test_sync_tracks_user_syncs(self, plexsync, track_factory, trackpair_factory):
        track = track_factory()
        pair = trackpair_factory(source_track=track, sync_state=SyncState.NEEDS_UPDATE)
        plexsync.source_player.search_tracks.return_value = [track]
        plexsync._match_tracks = MagicMock(return_value=[pair])
        plexsync._prompt_user_action = MagicMock(return_value=[pair])
        plexsync.config_mgr.dry = False
        plexsync._sync_ratings = MagicMock()
        plexsync.sync_tracks()
        plexsync._sync_ratings.assert_called_once_with([pair])

    def test_sync_tracks_user_syncs_dry_run(self, plexsync, track_factory, trackpair_factory):
        track = track_factory()
        pair = trackpair_factory(source_track=track, sync_state=SyncState.NEEDS_UPDATE)
        plexsync.source_player.search_tracks.return_value = [track]
        plexsync._match_tracks = MagicMock(return_value=[pair])
        plexsync._prompt_user_action = MagicMock(return_value=[pair])
        plexsync.config_mgr.dry = True
        plexsync._sync_ratings = MagicMock()
        plexsync.sync_tracks()
        plexsync._sync_ratings.assert_called_once_with([pair])
        with patch("builtins.print") as mock_print:
            plexsync.sync_tracks()
            mock_print.assert_any_call("[DRY RUN] No changes will be written.")

    def test_match_tracks_empty_list_returns_empty(self, plexsync):
        result = plexsync._match_tracks([])
        assert result == []

    def test_match_tracks_all_tracks_matched(self, plexsync, track_factory):
        track1 = track_factory()
        track2 = track_factory()
        track1._pair_state = SyncState.UP_TO_DATE
        track2._pair_state = SyncState.UP_TO_DATE
        pairs = plexsync._match_tracks([track1, track2])
        assert len(pairs) == 2
        assert all(p.source == t for p, t in zip(pairs, [track1, track2], strict=True))
        assert all(p.sync_state == SyncState.UP_TO_DATE for p in pairs)

    def test_match_tracks_some_tracks_unmatched(self, plexsync, track_factory):
        track1 = track_factory()
        track2 = track_factory()
        track1._pair_state = SyncState.UP_TO_DATE
        # track2 has no _pair_state, so will default to whatever TrackPair does (likely unmatched)
        pairs = plexsync._match_tracks([track1, track2])
        # Only track1 should be considered matched (if _match_tracks filters by sync_state)
        assert any(p.source == track1 and p.sync_state == SyncState.UP_TO_DATE for p in pairs)
        assert all(p.source != track2 or p.sync_state != SyncState.UP_TO_DATE for p in pairs)

    def test_match_tracks_all_tracks_unmatched(self, plexsync, track_factory):
        track1 = track_factory()
        track2 = track_factory()
        # Neither track has _pair_state set, so both should be unmatched
        pairs = plexsync._match_tracks([track1, track2])
        # Should be empty or all pairs have default state (depending on _match_tracks logic)
        assert all(getattr(p, "sync_state", None) != SyncState.UP_TO_DATE for p in pairs)

    def test_match_tracks_various_sync_states(self, plexsync, track_factory):
        track1 = track_factory()
        track2 = track_factory()
        track1._pair_state = SyncState.UP_TO_DATE
        track2._pair_state = SyncState.CONFLICTING
        pairs = plexsync._match_tracks([track1, track2])
        assert len(pairs) == 2
        assert pairs[0].sync_state == SyncState.UP_TO_DATE
        assert pairs[1].sync_state == SyncState.CONFLICTING

    def test_sync_ratings_empty_pairs_noop(self, plexsync):
        plexsync.logger.getEffectiveLevel.return_value = 20  # logging.INFO
        with patch("builtins.print") as mock_print:
            plexsync._sync_ratings([])
            mock_print.assert_any_call("No applicable tracks to update.")
        plexsync.logger.info.assert_not_called()
        plexsync.status_mgr.start_phase.assert_not_called()

    def test_sync_ratings_dry_run_no_sync(self, plexsync, track_factory, trackpair_factory):
        pair = trackpair_factory()
        plexsync.config_mgr.dry = True
        plexsync.logger.getEffectiveLevel.return_value = 20  # logging.INFO
        pair.sync = MagicMock()
        with patch("builtins.print") as mock_print:
            plexsync._sync_ratings([pair])
            mock_print.assert_any_call("[DRY RUN] No changes will be written.")
        pair.sync.assert_called_once()

    def test_sync_ratings_logger_above_info_skips_info_log(self, plexsync, track_factory, trackpair_factory):
        pair = trackpair_factory()
        plexsync.config_mgr.dry = False
        plexsync.logger.getEffectiveLevel.return_value = 30  # logging.WARNING
        pair.sync = MagicMock()
        with patch("builtins.print") as mock_print:
            plexsync._sync_ratings([pair])
            assert any("Syncing 1 tracks" in str(c.args[0]) and "using direction:" in str(c.args[0]) for c in mock_print.call_args_list)
        pair.sync.assert_called_once()


class TestPlaylistSync:
    def test_sync_playlists_none_warns(self, plexsync):
        plexsync.source_player.search_playlists.return_value = []
        plexsync.sync_playlists()
        plexsync.logger.warning.assert_called_once_with("No playlists found")

    def test_sync_playlists_workflow(self, plexsync, playlistpair_factory, playlist_factory):
        playlist1 = playlist_factory(ID="pl1", name="Playlist1")
        playlist2 = playlist_factory(ID="pl2", name="Playlist2")
        plexsync.source_player.search_playlists.return_value = [playlist1, playlist2]
        plexsync.config_mgr.dry = False
        plexsync.sync_playlists()
        plexsync.stats_mgr.increment.assert_any_call("playlists_processed", 2)
        plexsync.logger.info.assert_any_call(f"Matching {plexsync.source_player.name()} playlists with {plexsync.destination_player.name()}")

    def test_sync_playlists_filter_auto(self, plexsync, playlistpair_factory, playlist_factory):
        auto_playlist = playlist_factory(ID="auto1", name="AutoPlaylist", is_auto_playlist=True)
        normal_playlist = playlist_factory(ID="norm1", name="NormalPlaylist", is_auto_playlist=False)
        plexsync.source_player.search_playlists.return_value = [auto_playlist, normal_playlist]
        plexsync.config_mgr.dry = False
        plexsync.sync_playlists()
        plexsync.stats_mgr.increment.assert_any_call("playlists_processed", 1)

    def test_sync_playlists_dry_run(self, plexsync, playlistpair_factory, playlist_factory):
        playlist = playlist_factory(ID="pl1", name="Playlist1")
        plexsync.source_player.search_playlists.return_value = [playlist]
        plexsync.config_mgr.dry = True
        plexsync.sync_playlists()
        plexsync.logger.info.assert_any_call("Running a DRY RUN. No changes will be propagated!")


class TestUserPrompt:
    def test_prompt_filter_sync_toggle_all_options(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        plexsync.track_filter = {"reverse": False, "sync_conflicts": False, "include_unrated": False, "quality": None}
        assert plexsync.track_filter["reverse"] is False
        assert plexsync.track_filter["sync_conflicts"] is False
        assert plexsync.track_filter["include_unrated"] is False
        assert plexsync.track_filter["quality"] is None
        PROMPT_RESPONSES[:] = ["filter", "reverse", "conflicts", "unrated", "quality", "good", "cancel", "cancel"]
        plexsync._prompt_user_action()
        assert plexsync.track_filter["reverse"] is True
        assert plexsync.track_filter["sync_conflicts"] is True
        assert plexsync.track_filter["include_unrated"] is True
        assert plexsync.track_filter["quality"] == MatchThreshold.GOOD_MATCH

    def test_prompt_detailed_view_switch_to_unrated(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        plexsync._display_trackpair_list = MagicMock()
        PROMPT_RESPONSES[:] = ["details", "switch_unrated", "Perfect Matches", "cancel", "cancel"]
        plexsync._prompt_user_action()
        plexsync._display_trackpair_list.assert_called_once()
        call_args = plexsync._display_trackpair_list.call_args
        assert "Only unrated destination tracks" in call_args[0][1]

    def test_prompt_detailed_view_switch_to_conflicting(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        plexsync._display_trackpair_list = MagicMock()
        PROMPT_RESPONSES[:] = ["details", "switch_conflicting", "Perfect Matches", "cancel", "cancel"]
        plexsync._prompt_user_action()
        plexsync._display_trackpair_list.assert_called_once()
        call_args = plexsync._display_trackpair_list.call_args
        assert "Only tracks with rating conflicts" in call_args[0][1]

    def test_prompt_detailed_view_switch_to_all(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        plexsync._display_trackpair_list = MagicMock()
        PROMPT_RESPONSES[:] = ["details", "switch_all", "Perfect Matches", "cancel", "cancel"]
        plexsync._prompt_user_action()
        plexsync._display_trackpair_list.assert_called_once()
        call_args = plexsync._display_trackpair_list.call_args
        assert "All discovered tracks" in call_args[0][1]

    def test_prompt_detailed_view_cancel_no_change(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        plexsync._display_trackpair_list = MagicMock()
        PROMPT_RESPONSES[:] = ["details", "cancel", "cancel"]
        plexsync._prompt_user_action()
        plexsync._display_trackpair_list.assert_not_called()

    def test_prompt_detailed_view_invalid_scope(self, plexsync, trackpairs):
        """Test _prompt_detailed_view when invalid scope is provided"""
        plexsync.sync_pairs = trackpairs
        plexsync._display_trackpair_list = MagicMock()
        PROMPT_RESPONSES[:] = ["details", "switch_invalid", "cancel", "cancel"]
        plexsync._prompt_user_action()
        plexsync._display_trackpair_list.assert_not_called()

    def test_user_prompt_sync_returns_pairs(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        plexsync._user_prompt_templates = {k: k for k in plexsync._user_prompt_templates}
        PROMPT_RESPONSES[:] = ["sync"]
        result = plexsync._prompt_user_action()
        expected = [p for p in trackpairs if p.sync_state in (SyncState.NEEDS_UPDATE, SyncState.CONFLICTING)]
        assert result == expected, f"Expected {expected}, got {result}"

    def test_user_prompt_filter_cancel_returns_none(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        plexsync._user_prompt_templates = {k: k for k in plexsync._user_prompt_templates}
        PROMPT_RESPONSES[:] = ["filter", "cancel", "cancel"]
        result = plexsync._prompt_user_action()
        assert result is None

    def test_user_prompt_manual_cancel_returns_none(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        PROMPT_RESPONSES[:] = ["manual", "cancel"]
        result = plexsync._prompt_user_action()
        assert result == []

    def test_user_prompt_details_cancel_returns_none(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        PROMPT_RESPONSES[:] = ["details", "cancel", "cancel"]
        result = plexsync._prompt_user_action()
        assert result is None

    def test_user_prompt_cancel_returns_none(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        PROMPT_RESPONSES[:] = ["cancel"]
        result = plexsync._prompt_user_action()
        assert result is None


class TestBuildUserPrompt:
    def test_has_conflicts(self, plexsync, trackpairs):
        plexsync.sync_pairs = [p for p in trackpairs if p.sync_state == SyncState.CONFLICTING]
        plexsync.track_filter["reverse"] = False
        plexsync.source_player.name.return_value = "SourcePlayer"
        plexsync.destination_player.name.return_value = "DestPlayer"

        plexsync._build_user_prompt()

        assert set(plexsync.user_prompt_options.keys()) == {"sync", "filter", "manual", "details", "cancel"}
        assert "from SourcePlayer to DestPlayer" in plexsync.user_prompt_options["sync"]

    def test_has_unrated_no_conflicts(self, plexsync, trackpairs):
        plexsync.sync_pairs = [p for p in trackpairs if p.sync_state == SyncState.NEEDS_UPDATE]
        plexsync.track_filter["reverse"] = True
        plexsync.source_player.name.return_value = "SourcePlayer"
        plexsync.destination_player.name.return_value = "DestPlayer"

        plexsync._build_user_prompt()

        assert set(plexsync.user_prompt_options.keys()) == {"sync", "filter", "details", "cancel"}
        assert "from DestPlayer to SourcePlayer" in plexsync.user_prompt_options["sync"]

    def test_no_unrated_no_conflicts(self, plexsync, trackpairs):
        plexsync.sync_pairs = [p for p in trackpairs if p.sync_state == SyncState.UP_TO_DATE]

        plexsync._build_user_prompt()

        assert set(plexsync.user_prompt_options.keys()) == {"sync", "filter", "cancel"}

    def test_empty_sync_pairs(self, plexsync):
        plexsync.sync_pairs = []
        plexsync.user_prompt_options = {"existing": "option"}

        plexsync._build_user_prompt()

        assert set(plexsync.user_prompt_options.keys()) == {"filter", "cancel"}


class TestConflictResolution:
    def test_conflict_manual_src_to_dst(self, plexsync, trackpair_factory):
        conflict_pair = trackpair_factory(sync_state=SyncState.CONFLICTING)
        plexsync.sync_pairs = [conflict_pair]

        # Assert pre-conditions: should start with one conflict, no resolved pairs
        assert len(plexsync.conflicts) == 1
        assert conflict_pair in plexsync.conflicts
        original_source = conflict_pair.source
        original_destination = conflict_pair.destination

        PROMPT_RESPONSES[:] = ["manual", "src_to_dst"]
        result = plexsync._prompt_user_action()

        # Should return exactly one pair - the original conflict pair unmodified
        assert len(result) == 1
        assert result[0] is conflict_pair  # Same object reference
        assert result[0].source is original_source  # Source unchanged
        assert result[0].destination is original_destination  # Destination unchanged

    def test_conflict_manual_dst_to_src(self, plexsync, trackpair_factory):
        conflict_pair = trackpair_factory(sync_state=SyncState.CONFLICTING)
        plexsync.sync_pairs = [conflict_pair]

        # Assert pre-conditions: should start with one conflict
        assert len(plexsync.conflicts) == 1
        assert conflict_pair in plexsync.conflicts
        original_source = conflict_pair.source
        original_destination = conflict_pair.destination

        PROMPT_RESPONSES[:] = ["manual", "dst_to_src"]
        result = plexsync._prompt_user_action()

        # Should return exactly one pair - a reversed version of the conflict pair
        assert len(result) == 1
        assert result[0] is not conflict_pair  # Different object (reversed)
        assert result[0].source is original_destination  # Source becomes original destination
        assert result[0].destination is original_source  # Destination becomes original source

    def test_conflict_manual_custom_rating(self, plexsync, trackpair_factory):
        conflict_pair = trackpair_factory(sync_state=SyncState.CONFLICTING)
        plexsync.sync_pairs = [conflict_pair]

        # Assert pre-conditions: should start with one conflict and original ratings
        assert len(plexsync.conflicts) == 1
        assert conflict_pair in plexsync.conflicts
        original_source_rating = conflict_pair.source.rating.to_float()
        original_dest_rating = conflict_pair.destination.rating.to_float()
        # Verify ratings are not already 4.5
        assert original_source_rating != 4.5
        assert original_dest_rating != 4.5

        PROMPT_RESPONSES[:] = ["manual", "manual", "4.5"]
        result = plexsync._prompt_user_action()

        # Should return both original and reversed pairs with new rating injected
        assert len(result) == 2
        assert result[0].source.rating.to_float() == 4.5
        assert result[0].destination.rating.to_float() == 4.5

    def test_conflict_manual_skip(self, plexsync, trackpair_factory):
        conflict_pair = trackpair_factory(sync_state=SyncState.CONFLICTING)
        plexsync.sync_pairs = [conflict_pair]

        # Assert pre-conditions: should start with one conflict
        assert len(plexsync.conflicts) == 1
        assert conflict_pair in plexsync.conflicts
        original_source = conflict_pair.source
        original_destination = conflict_pair.destination

        PROMPT_RESPONSES[:] = ["manual", "skip"]
        result = plexsync._prompt_user_action()

        # Skip should return empty list and leave original pair unchanged
        assert result == []
        # Verify original pair remains unchanged
        assert conflict_pair.source is original_source
        assert conflict_pair.destination is original_destination

    def test_conflict_manual_cancel(self, plexsync, trackpair_factory):
        conflict_pair1 = trackpair_factory(sync_state=SyncState.CONFLICTING)
        conflict_pair2 = trackpair_factory(sync_state=SyncState.CONFLICTING)
        plexsync.sync_pairs = [conflict_pair1, conflict_pair2]

        # Assert pre-conditions: should start with two conflicts
        assert len(plexsync.conflicts) == 2
        assert conflict_pair1 in plexsync.conflicts
        assert conflict_pair2 in plexsync.conflicts

        PROMPT_RESPONSES[:] = ["manual", "src_to_dst", "cancel"]
        result = plexsync._prompt_user_action()

        # Should resolve first conflict then cancel, returning only first pair
        assert len(result) == 1
        assert result[0] is conflict_pair1

    def test_conflict_manual_none_rating(self, plexsync, trackpair_factory):
        conflict_pair = trackpair_factory(sync_state=SyncState.CONFLICTING)
        plexsync.sync_pairs = [conflict_pair]

        # Assert pre-conditions: should start with one conflict
        assert len(plexsync.conflicts) == 1
        assert conflict_pair in plexsync.conflicts
        original_source = conflict_pair.source
        original_destination = conflict_pair.destination

        # Mock text to return None (user cancels)
        plexsync.prompt.text = lambda *args, **kwargs: None
        PROMPT_RESPONSES[:] = ["manual", "manual"]
        result = plexsync._prompt_user_action()

        # Should return empty list when user cancels rating input and leave pair unchanged
        assert result == []
        # Verify original pair remains unchanged
        assert conflict_pair.source is original_source
        assert conflict_pair.destination is original_destination

    def test_conflict_multiple_resolutions(self, plexsync, trackpair_factory, audio_tag_factory):
        conflict_pairs = []
        for i in range(1, 10):
            source_tag = audio_tag_factory(ID=i * 10, title=f"Source{i}", artist=f"Artist{i}")
            dest_tag = audio_tag_factory(ID=i * 10 + 1, title=f"Dest{i}", artist=f"Artist{i}")
            pair = trackpair_factory(source_track=source_tag, destination_track=dest_tag, sync_state=SyncState.CONFLICTING)
            conflict_pairs.append(pair)

        plexsync.sync_pairs = conflict_pairs
        assert len(plexsync.conflicts) == 9

        PROMPT_RESPONSES[:] = ["manual", "src_to_dst", "dst_to_src", "skip", "manual", "3.5", "dst_to_src", "skip", "manual", None, "cancel"]

        result = plexsync._prompt_user_action()

        assert len(result) == 5

        pair1_result = next(r for r in result if r.source.ID == 10)
        assert pair1_result is conflict_pairs[0]

        pair2_result = next(r for r in result if r.source.ID == 21)
        assert pair2_result is not conflict_pairs[1]
        assert pair2_result.destination.ID == 20

        assert conflict_pairs[3].source.rating.to_float() == 3.5
        assert conflict_pairs[3].destination.rating.to_float() == 3.5

        pair4_results = [r for r in result if r.source.rating.to_float() == 3.5]
        assert len(pair4_results) == 2

        pair4_original = next(r for r in pair4_results if r.source.ID == 40)
        pair4_reversed = next(r for r in pair4_results if r.source.ID == 41)
        assert pair4_original.destination.ID == 41
        assert pair4_reversed.destination.ID == 40

        pair5_result = next(r for r in result if r.source.ID == 51)
        assert pair5_result is not conflict_pairs[4]
        assert pair5_result.destination.ID == 50


class TestDescribeSync:
    @pytest.mark.parametrize(
        "track_filter,expected",
        [
            ({"include_unrated": True, "sync_conflicts": True, "quality": None}, "unrated and conflicting tracks"),
            ({"include_unrated": False, "sync_conflicts": True, "quality": None}, "conflicting tracks"),
            ({"include_unrated": True, "sync_conflicts": False, "quality": None}, "unrated tracks"),
            ({"include_unrated": False, "sync_conflicts": False, "quality": None}, "no tracks"),
            ({"include_unrated": True, "sync_conflicts": True, "quality": "PERFECT_MATCH"}, "unrated and PERFECT_MATCH+ conflicting tracks"),
        ],
    )
    def test_describe_sync_filters(self, plexsync, track_filter, expected):
        assert plexsync._describe_sync(track_filter) == expected


class TestProperties:
    def test_conflicts_property(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        result = plexsync.conflicts
        assert all(p.sync_state == SyncState.CONFLICTING for p in result)
        assert len(result) == sum(p.sync_state == SyncState.CONFLICTING for p in trackpairs)

    def test_unrated_property(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        result = plexsync.unrated
        assert all(p.sync_state == SyncState.NEEDS_UPDATE for p in result)
        assert len(result) == 3


class TestGetTrackFilter:
    def test_track_filter_excludes_unmatched(self, plexsync, trackpairs):
        filter_fn = plexsync._get_track_filter(include_unmatched=False)
        result = [p for p in trackpairs if filter_fn(p)]
        assert all(p.sync_state != SyncState.UNKNOWN for p in result)

    def test_track_filter_quality(self, plexsync, trackpairs):
        filter_fn = plexsync._get_track_filter(quality=MatchThreshold.PERFECT_MATCH)
        result = [p for p in trackpairs if filter_fn(p)]
        assert all(p.quality == MatchThreshold.PERFECT_MATCH for p in result)
        assert len(result) == 2  # Updated: UP_TO_DATE and NEEDS_UPDATE with score=100

    def test_track_filter_conflict_setting(self, plexsync, trackpairs):
        filter_fn = plexsync._get_track_filter(include_conflicts=False)
        result = [p for p in trackpairs if filter_fn(p)]
        assert all(p.sync_state != SyncState.CONFLICTING for p in result)


class TestGetMatchDisplayOptions:
    def test_match_display_options_all_scope_perfect_good_unmatched(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        options, filters = plexsync._get_match_display_options("all")
        assert set(filters.keys()) == {"Perfect Matches", "Good Matches", "Poor Matches", "Unmatched"}
        for cat in ["Perfect Matches", "Good Matches", "Poor Matches", "Unmatched"]:
            assert any(filters[cat](p) for p in trackpairs)

    def test_match_display_options_all_scope_only_unmatched(self, plexsync, trackpairs):
        unmatched = [p for p in trackpairs if p.sync_state in (SyncState.UNKNOWN, SyncState.ERROR)]
        plexsync.sync_pairs = unmatched
        options, filters = plexsync._get_match_display_options("all")
        assert [opt.label for opt in options] == [f"Unmatched ({len(unmatched)})"]
        assert set(filters.keys()) == {"Perfect Matches", "Good Matches", "Poor Matches", "Unmatched"}
        assert all(filters["Unmatched"](p) for p in unmatched)

    def test_match_display_options_unrated_scope_perfect_good(self, plexsync, trackpairs):
        unrated = [p for p in trackpairs if p.sync_state == SyncState.NEEDS_UPDATE and p.quality in (MatchThreshold.PERFECT_MATCH, MatchThreshold.GOOD_MATCH)]
        plexsync.sync_pairs = unrated + [p for p in trackpairs if p.sync_state == SyncState.UP_TO_DATE]
        _options, filters = plexsync._get_match_display_options("unrated")
        assert set(filters.keys()) == {"Perfect Matches", "Good Matches", "Poor Matches"}
        for cat, threshold in zip(["Perfect Matches", "Good Matches"], [MatchThreshold.PERFECT_MATCH, MatchThreshold.GOOD_MATCH], strict=False):
            assert any(filters[cat](p) for p in unrated if p.quality == threshold)

    def test_match_display_options_conflicting_scope_good_poor(self, plexsync, trackpairs):
        conflicting = [p for p in trackpairs if p.sync_state == SyncState.CONFLICTING and p.quality in (MatchThreshold.GOOD_MATCH, MatchThreshold.POOR_MATCH)]
        plexsync.sync_pairs = conflicting + [p for p in trackpairs if p.sync_state == SyncState.UP_TO_DATE]
        options, filters = plexsync._get_match_display_options("conflicting")
        assert set(filters.keys()) == {"Perfect Matches", "Good Matches", "Poor Matches"}
        for cat, threshold in zip(["Good Matches", "Poor Matches"], [MatchThreshold.GOOD_MATCH, MatchThreshold.POOR_MATCH], strict=False):
            assert any(filters[cat](p) for p in conflicting if p.quality == threshold)

    def test_match_display_options_all_scope_empty_input(self, plexsync):
        plexsync.sync_pairs = []
        options, filters = plexsync._get_match_display_options("all")
        assert options == []
        assert set(filters.keys()) == {"Perfect Matches", "Good Matches", "Poor Matches", "Unmatched"}

    def test_match_display_options_invalid_scope_returns_empty(self, plexsync, trackpairs):
        plexsync.sync_pairs = trackpairs
        options, filters = plexsync._get_match_display_options("invalid_scope")
        assert options == []
        assert set(filters.keys()) == {"Perfect Matches", "Good Matches", "Poor Matches"}
        for cat in filters:
            assert all(not filters[cat](p) for p in trackpairs)


class TestFilterSyncPairs:
    @pytest.mark.parametrize(
        "track_filter,expected_counts",
        [
            (
                {"include_unrated": True, "sync_conflicts": True, "quality": None, "reverse": False},
                {SyncState.NEEDS_UPDATE: 3, SyncState.CONFLICTING: 3},
            ),
            (
                {"include_unrated": False, "sync_conflicts": True, "quality": None, "reverse": False},
                {SyncState.CONFLICTING: 3},
            ),
            (
                {"include_unrated": True, "sync_conflicts": False, "quality": None, "reverse": False},
                {SyncState.NEEDS_UPDATE: 3},
            ),
            (
                {"include_unrated": True, "sync_conflicts": True, "quality": MatchThreshold.GOOD_MATCH, "reverse": False},
                {SyncState.NEEDS_UPDATE: 2, SyncState.CONFLICTING: 2},
            ),
            (
                {"include_unrated": True, "sync_conflicts": True, "quality": None, "reverse": False},
                {SyncState.NEEDS_UPDATE: 3, SyncState.CONFLICTING: 3},
            ),
            (
                {"include_unrated": False, "sync_conflicts": False, "quality": None, "reverse": False},
                {},
            ),
        ],
    )
    def test_filter_sync_pairs_various_filters(self, plexsync, trackpairs, track_filter, expected_counts):
        plexsync.sync_pairs = trackpairs
        plexsync.track_filter = track_filter.copy()
        result = plexsync._filter_sync_pairs()
        from collections import Counter

        state_counts = Counter(p.sync_state for p in result)
        assert state_counts == expected_counts
        # Also assert no unexpected states
        assert set(state_counts.keys()) <= set(expected_counts.keys())

    def test_filter_sync_pairs_excludes_unmatched_and_error(self, plexsync, trackpairs):
        """Test that unmatched and error states are always excluded by the filter."""
        plexsync.sync_pairs = trackpairs
        plexsync.track_filter = {"include_unrated": True, "sync_conflicts": True, "quality": None, "reverse": False}
        result = plexsync._filter_sync_pairs()
        for p in result:
            assert p.sync_state not in (SyncState.UNKNOWN, SyncState.ERROR)

    def test_filter_sync_pairs_reverse_returns_swapped_source_and_destination(self, plexsync, trackpairs):
        """Test that reverse filter returns real reversed objects with swapped source and destination for all applicable sync_pairs."""
        plexsync.sync_pairs = trackpairs
        plexsync.track_filter = {"include_unrated": True, "sync_conflicts": True, "quality": None, "reverse": True}
        result = plexsync._filter_sync_pairs()
        expected_pairs = [p for p in trackpairs if p.sync_state in (SyncState.NEEDS_UPDATE, SyncState.CONFLICTING)]
        assert len(result) == len(expected_pairs)
        for reversed_pair, orig_pair in zip(result, expected_pairs, strict=True):
            assert reversed_pair.source == orig_pair.destination
            assert reversed_pair.destination == orig_pair.source
            assert reversed_pair.sync_state == orig_pair.sync_state
            assert getattr(reversed_pair, "quality", None) == getattr(orig_pair, "quality", None)
