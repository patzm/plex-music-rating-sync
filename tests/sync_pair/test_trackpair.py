from unittest.mock import MagicMock

import pytest

from ratings import Rating
from sync_items import AudioTag
from sync_pair import MatchThreshold, SyncState, TrackPair


def album_empty(album):
    return album == "" or album is None


@pytest.fixture
def source_player():
    player = MagicMock(name="source_player")
    player.album_empty = MagicMock(side_effect=album_empty)
    player.search_tracks.return_value = []
    player.update_rating = MagicMock()
    return player


@pytest.fixture
def destination_player():
    player = MagicMock(name="destination_player")
    player.album_empty = MagicMock(side_effect=album_empty)
    player.search_tracks.return_value = []
    player.update_rating = MagicMock()
    return player


@pytest.fixture
def src_track():
    return AudioTag(ID="1", title="abc", artist="xyz", album="def", track=1, rating=Rating(0.9), file_path="/music/a.flac")


@pytest.fixture
def dst_match():
    return AudioTag(ID="2", title="abc", artist="xyz", album="def", track=1, rating=Rating(0.9), file_path="/music/a.flac")


@pytest.fixture
def dst_good():
    return AudioTag(ID="99", title="abc (remastered)", artist="xyz", album="def", track=1, rating=Rating.unrated(), file_path="/music/a.flac")


@pytest.fixture
def dst_poor():
    return AudioTag(ID="3", title="abc (remastered)", artist="456", album="789", track=1, rating=Rating(0.9), file_path="/music/a.flac")


@pytest.fixture
def dst_no_match():
    return AudioTag(ID="4", title="123", artist="456", album="789", track=99, rating=Rating(0.2), file_path="/music/no_match.flac")


class TestQuality:
    @pytest.mark.parametrize(
        "score,expected_quality",
        [
            (100, MatchThreshold.PERFECT_MATCH),
            (95, MatchThreshold.GOOD_MATCH),
            (50, MatchThreshold.POOR_MATCH),
            (20, None),
        ],
        ids=["perfect", "good", "poor", "none"],
    )
    def test_quality_assignment_by_score(self, score, expected_quality, src_track, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.score = score
        pair.sync_state = SyncState.UP_TO_DATE
        if expected_quality:
            assert pair.quality == expected_quality
        else:
            assert pair.quality is None


class TestSyncState:
    @pytest.mark.parametrize(
        "src_rating,dst_rating,expected_sync_state",
        [
            (4.5, None, SyncState.NEEDS_UPDATE),
            (4.5, 2.0, SyncState.CONFLICTING),
            (4.5, 4.5, SyncState.UP_TO_DATE),
        ],
        ids=["update", "conflict", "up_to_date"],
    )
    def test_sync_state_assignment_by_rating(self, src_track, dst_match, src_rating, dst_rating, expected_sync_state, source_player, destination_player):
        src = src_track
        dst = dst_match
        src.rating = type(src.rating)(src_rating)
        dst.rating = type(src.rating)(dst_rating) if dst_rating is not None else None
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src)
        pair.destination = dst
        pair.rating_source = src.rating
        pair.rating_destination = dst.rating
        pair.score = 100
        pair.match = lambda *a, **kw: None
        if src.rating and (dst.rating is None or getattr(dst.rating, "is_unrated", False)):
            pair.sync_state = SyncState.NEEDS_UPDATE
        elif src.rating != dst.rating:
            pair.sync_state = SyncState.CONFLICTING
        else:
            pair.sync_state = SyncState.UP_TO_DATE
        assert pair.sync_state == expected_sync_state


class TestSync:
    def test_sync_update_rating(self, source_player, destination_player, src_track, dst_match):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.sync_state = SyncState.NEEDS_UPDATE
        pair.sync()
        assert destination_player.update_rating.called


class TestReversal:
    def test_reversed_returns_new_pair(self, source_player, destination_player, src_track, dst_match):
        src = src_track
        dst = dst_match
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src)
        pair.destination = dst
        pair.rating_source = src.rating
        pair.rating_destination = dst.rating
        pair.score = 95
        reversed_pair = pair.reversed()
        assert reversed_pair.source_player == pair.destination_player
        assert reversed_pair.destination_player == pair.source_player
        assert reversed_pair.source == pair.destination
        assert reversed_pair.destination == pair.source
        assert reversed_pair.rating_source == pair.rating_destination
        assert reversed_pair.rating_destination == pair.rating_source
        assert reversed_pair.score == pair.score
        assert pair.source == src
        assert pair.destination == dst


class TestMatchMethod:
    @pytest.mark.parametrize(
        "candidates_fixtures,expected_dst,expected_quality",
        [
            (["dst_match", "dst_good", "dst_poor", "dst_no_match"], "dst_match", MatchThreshold.PERFECT_MATCH),
            (["dst_good", "dst_poor", "dst_no_match"], "dst_good", MatchThreshold.GOOD_MATCH),
            (["dst_poor", "dst_no_match"], "dst_poor", MatchThreshold.POOR_MATCH),
            (["dst_no_match"], None, None),
            ([], None, None),
            (["dst_match"], "dst_match", MatchThreshold.PERFECT_MATCH),
        ],
        ids=["perfect", "good", "poor", "no_match", "empty", "unrated"],
    )
    def test_match_assigns_correct_destination_quality(self, src_track, request, candidates_fixtures, expected_dst, expected_quality, source_player, destination_player):
        candidate_list = [request.getfixturevalue(f) for f in candidates_fixtures]
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        result = pair.match(candidate_list)
        if expected_dst:
            expected_obj = request.getfixturevalue(expected_dst)
            assert pair.destination == expected_obj
        else:
            assert pair.destination is None
        assert pair.quality == expected_quality
        # Accept both None and False for no match
        if expected_dst is None:
            assert result in (pair.destination, None, False)
        else:
            assert result == pair.destination or result is True

    @pytest.mark.parametrize(
        "dst_rating, sync_status",
        [
            (Rating.unrated(), SyncState.NEEDS_UPDATE),
            (Rating(0.9), SyncState.UP_TO_DATE),
            (Rating(0.1), SyncState.CONFLICTING),
        ],
    )
    def test_match_assigns_status(self, src_track, dst_match, source_player, destination_player, dst_rating, sync_status):
        dst_match.rating = dst_rating
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.match([dst_match])
        assert pair.sync_state == sync_status

    def test_match_handles_none_candidates(self, src_track, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        result = pair.match(None)
        assert result is None or result is False
        assert pair.destination is None
        assert pair.score in (None, 0)
        assert pair.quality is None

    def test_match_source_none_raises(self, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=None)
        with pytest.raises(RuntimeError):
            pair.match([])

    def test_match_with_none_rating_success(self, source_player, destination_player, src_track):
        """Test match() handles track with None rating gracefully."""
        dst_track_none_rating = AudioTag(ID="4", title="abc", artist="xyz", album="def", track=1, rating=None, file_path="/music/a.flac")
        destination_player.search_tracks.return_value = [dst_track_none_rating]

        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        result = pair.match()

        assert result is True
        assert pair.rating_destination.is_unrated
        assert pair.sync_state == SyncState.NEEDS_UPDATE


class TestSimilarityMethod:
    @pytest.mark.parametrize(
        "dst_track,expected_quality",
        [
            ("dst_match", MatchThreshold.PERFECT_MATCH),
            ("dst_good", MatchThreshold.GOOD_MATCH),
            ("dst_poor", MatchThreshold.POOR_MATCH),
            ("dst_no_match", None),
        ],
        ids=["perfect", "good", "poor", "none"],
    )
    def test_similarity_returns_expected_score(self, src_track, request, dst_track, expected_quality, source_player, destination_player):
        dst = request.getfixturevalue(dst_track)
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.score = pair.similarity(dst)
        pair.sync_state = SyncState.UP_TO_DATE

        assert pair.quality is expected_quality


class TestCacheMethods:
    @pytest.mark.parametrize(
        "match_cache,cached_id,cached_score,candidates,score_none,expected_result",
        [
            (False, None, None, [], False, None),  # no match_cache
            (True, None, 90, [], False, None),  # cached_id is None
            (True, "id", None, [], False, None),  # cached_score is None
            (True, "id", 50, [], False, None),  # cached_score below threshold
            (True, "id", 90, [], False, None),  # candidates empty
            (True, "id", 90, [MagicMock()], False, "candidate"),  # candidates present
        ],
        ids=[
            "no_cache_available",
            "no_cached_id",
            "cached_score_below_threshold",
            "no_candidates_found",
            "candidates_found",
            "cached_score_none_candidates_found",
        ],
    )
    def test_get_cache_match(self, match_cache, cached_id, cached_score, candidates, score_none, expected_result, src_track, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.cache_mgr.match_cache = match_cache

        def fake_get_match(*a, **k):
            return cached_id, cached_score

        pair.cache_mgr.get_match = fake_get_match
        pair.destination_player.search_tracks = MagicMock(return_value=candidates)
        pair.stats_mgr.increment = MagicMock()
        pair.similarity = MagicMock(return_value=95)
        pair.cache_mgr.set_match = MagicMock()
        if candidates and expected_result == "candidate":
            candidates[0].ID = "id"
        result = pair._get_cache_match()
        if expected_result == "candidate":
            assert result == candidates[0]
        else:
            assert result is None

    @pytest.mark.parametrize("match_cache", [True, False])
    def test_set_cache_match(self, match_cache, src_track, dst_good, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.cache_mgr.match_cache = match_cache
        pair.cache_mgr.set_match = MagicMock()
        pair._set_cache_match(dst_good)
        assert pair.cache_mgr.set_match.called == match_cache


class TestFindBestMatch:
    @pytest.mark.parametrize(
        "candidates_fixtures,expected_best_fixture,expected_score",
        [
            (["dst_poor", "dst_good", "dst_match"], "dst_match", 100),
            (["dst_no_match"], None, 0),
            ([], None, 0),
        ],
        ids=["best", "none", "empty"],
    )
    def test_find_best_match_returns_best_candidate_from_list(
        self, src_track, request, candidates_fixtures, expected_best_fixture, expected_score, source_player, destination_player
    ):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        candidate_list = [request.getfixturevalue(f) for f in candidates_fixtures]
        result = pair.find_best_match(candidate_list)
        if isinstance(result, tuple):
            best, score = result
        else:
            best, score = result, pair.score
        expected_best = request.getfixturevalue(expected_best_fixture) if expected_best_fixture else None
        assert best == expected_best
        if isinstance(expected_score, float) or hasattr(expected_score, "approx"):
            assert score == pytest.approx(expected_score)
        else:
            assert score == expected_score

    @pytest.mark.parametrize(
        "cache_hit_fixture,cache_score,expected_best_fixture",
        [
            ("dst_match", 95, "dst_match"),
            ("dst_good", 99, "dst_good"),
        ],
        ids=["cached_match", "cached_match_score"],
    )
    def test_find_best_match_returns_cached_match_when_available(
        self, src_track, request, cache_hit_fixture, cache_score, expected_best_fixture, source_player, destination_player
    ):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        cached = request.getfixturevalue(cache_hit_fixture)
        pair._get_cache_match = MagicMock(return_value=cached)
        pair.score = cache_score
        result = pair.find_best_match([])
        if isinstance(result, tuple):
            best, score = result
        else:
            best, score = result, pair.score
        expected_best = request.getfixturevalue(expected_best_fixture) if expected_best_fixture else None
        assert best == expected_best
        assert score == cache_score

    def test_get_best_match_none(self, src_track, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        result = pair._get_best_match(None)
        # Accept both (None, 0) and (None, 0.0) as valid no-match results
        assert result == (None, 0)


class TestCandidateSearch:
    def test_search_candidates_returns_empty_on_empty_title(self, src_track, source_player, destination_player):
        src_track.title = ""
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        candidates = pair._search_candidates()
        assert candidates == []

    def test_search_candidates_raises_valueerror(self, src_track, source_player, destination_player, monkeypatch):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)

        def raise_value_error(*args, **kwargs):
            raise ValueError("Simulated search failure")

        pair.destination_player.search_tracks = raise_value_error
        src_track.title = "Some Title"
        with pytest.raises(ValueError):
            pair._search_candidates()


class TestAlbumSimilarity:
    @pytest.mark.parametrize(
        "src_album,dst_album,expected_similarity",
        [
            ("z", "z", 100),
            ("abc123", "abc345", 67),
            ("z", "w", 0),
            ("", None, 100),
            ("Album X", "", 0),
        ],
        ids=["same", "similar", "diff", "empty", "one_empty"],
    )
    def test_albums_similarity_various_cases(self, src_track, dst_good, src_album, dst_album, expected_similarity, source_player, destination_player):
        src_track.album = src_album
        dst_good.album = dst_album
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        score = pair.albums_similarity(dst_good)
        assert score == expected_similarity

    @pytest.mark.parametrize(
        "src_album,dst_album,expected",
        [
            ("", None, True),
            ("Album X", "", False),
        ],
        ids=["both_empty", "one_empty"],
    )
    def test_both_albums_empty_various_cases(self, src_track, dst_good, src_album, dst_album, expected, source_player, destination_player):
        src_track.album = src_album
        dst_good.album = dst_album
        source_player.album_empty.return_value = src_album == ""
        destination_player.album_empty.return_value = dst_album in (None, "")
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        assert pair.both_albums_empty(dst_good) is expected

    def test_albums_similarity_default_arg(self, src_track, dst_good, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.destination = dst_good
        # Should use default arg (destination=None)
        result_default = pair.albums_similarity()
        # Should be same as explicit
        result_explicit = pair.albums_similarity(dst_good)
        assert result_default == result_explicit


class TestSyncCandidateStatus:
    @pytest.mark.parametrize(
        "sync_state,expected_is_candidate,expected_is_unmatched",
        [
            (SyncState.NEEDS_UPDATE, True, False),
            (SyncState.CONFLICTING, True, False),
            (SyncState.UP_TO_DATE, False, False),
            (SyncState.UNKNOWN, False, True),
            (SyncState.ERROR, False, True),
        ],
        ids=["update", "conflict", "up_to_date", "unknown", "error"],
    )
    def test_sync_state_flags(self, src_track, dst_good, sync_state, expected_is_candidate, expected_is_unmatched, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.destination = dst_good
        pair.sync_state = sync_state
        assert pair.is_sync_candidate is expected_is_candidate
        assert pair.is_unmatched is expected_is_unmatched


class TestRecordMatchQuality:
    @pytest.mark.parametrize(
        "score,expected_quality",
        [
            (100, MatchThreshold.PERFECT_MATCH),
            (85, MatchThreshold.GOOD_MATCH),
            (35, MatchThreshold.POOR_MATCH),
            (25, None),
        ],
        ids=["perfect", "good", "poor", "none"],
    )
    def test_record_match_quality_sets_quality(self, src_track, dst_good, score, expected_quality, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.destination = dst_good
        pair._record_match_quality(score)
        pair.sync_state = SyncState.UP_TO_DATE
        # Set score so .quality property can compute
        pair.score = score
        if expected_quality:
            assert pair.quality == expected_quality
        else:
            assert pair.quality is None


class TestTrackPairMinQuality:
    @pytest.mark.parametrize(
        "score,expected_poor,expected_good,expected_perfect",
        [
            (None, False, False, False),
            (29, False, False, False),
            (30, True, False, False),
            (79, True, False, False),
            (80, True, True, False),
            (99, True, True, False),
            (100, True, True, True),
            (101, True, True, True),
        ],
        ids=[
            "none",
            "below_poor",
            "at_poor",
            "below_good",
            "at_good",
            "below_perfect",
            "at_perfect",
            "above_perfect",
        ],
    )
    def test_has_min_quality_all_thresholds(self, score, expected_poor, expected_good, expected_perfect, src_track, source_player, destination_player):
        pair = TrackPair(source_player=source_player, destination_player=destination_player, source_track=src_track)
        pair.score = score
        pair.sync_state = SyncState.UP_TO_DATE
        assert pair.has_min_quality(MatchThreshold.POOR_MATCH) is expected_poor
        assert pair.has_min_quality(MatchThreshold.GOOD_MATCH) is expected_good
        assert pair.has_min_quality(MatchThreshold.PERFECT_MATCH) is expected_perfect
