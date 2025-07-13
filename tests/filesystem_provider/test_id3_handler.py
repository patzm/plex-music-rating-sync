import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mutagen.id3 import COMM, POPM, TXXX
from mutagen.mp3 import MP3

from filesystem_provider import ID3, DefaultPlayerTags, ID3Field, ID3Handler
from manager.config_manager import ConflictResolutionStrategy, TagWriteStrategy
from ratings import Rating, RatingScale
from sync_items import AudioTag
from tests.helpers import add_or_update_id3frame, get_popm_email, make_raw_rating

PROMPT_RESPONSES = []


class DummyPrompt:
    def choice(self, prompt, options, **kwargs):
        return PROMPT_RESPONSES.pop(0) if PROMPT_RESPONSES else None

    def yes_no(self, *args, **kwargs):
        return PROMPT_RESPONSES.pop(0) if PROMPT_RESPONSES else None

    def text(self, *args, **kwargs):
        return PROMPT_RESPONSES.pop(0) if PROMPT_RESPONSES else None

    def confirm_continue(self, *args, **kwargs):
        return PROMPT_RESPONSES.pop(0) if PROMPT_RESPONSES else None


@pytest.fixture
def mp3_file_factory():
    # Create a temporary copy of test.mp3 for testing.
    test_mp3_path = Path("tests/test.mp3")

    def _factory(rating: float = 1.0, rating_tags: list[str] | str | None = None, **kwargs):
        fd, temp_path = tempfile.mkstemp(suffix=".mp3")
        temp_path = Path(temp_path)
        shutil.copyfile(test_mp3_path, temp_path)
        audio = MP3(temp_path)
        audio.save = MagicMock(side_effect=lambda *args, **kw: audio.save())

        if not rating_tags:
            rating_tags = [DefaultPlayerTags.TEXT, DefaultPlayerTags.MEDIAMONKEY]

        return add_or_update_id3frame(
            audio,
            title=kwargs.get("title", "Default Title"),
            artist=kwargs.get("artist", "Default Artist"),
            album=kwargs.get("album", "Default Album"),
            track=kwargs.get("track", "1/10"),
            rating=rating,
            rating_tags=rating_tags,
        )

    return _factory


@pytest.fixture
def handler(monkeypatch):
    # Mocked ID3Handler with DummyPrompt for DRY prompt mocking.
    handler = ID3Handler(
        tagging_policy={
            "conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST,
            "tag_write_strategy": TagWriteStrategy.WRITE_DEFAULT,
            "default_tag": "MEDIAMONKEY",
        }
    )
    handler.logger = MagicMock()
    handler.stats_mgr = MagicMock()
    handler.stats_mgr.get.return_value = {"TEXT": 5, "MEDIAMONKEY": 3}
    # Patch handler.prompt to use DummyPrompt
    monkeypatch.setattr(handler, "prompt", DummyPrompt())
    return handler


@pytest.fixture
def id3_file_with_tags(mp3_file_factory):
    return mp3_file_factory()


@pytest.fixture
def id3_file_without_tags(mp3_file_factory):
    # ID3FileType object without tags.
    audio = mp3_file_factory()
    audio.tags = None
    return audio


@pytest.fixture
def fake_file_with_tags():
    # Non-ID3 file with mock tags.
    fake = MagicMock()
    fake.tags = ID3()
    fake.tags[ID3Field.TITLE] = TXXX(encoding=3, text=["Fake Title"])
    fake.tags[ID3Field.ARTIST] = TXXX(encoding=3, text=["Fake Artist"])
    fake.tags[ID3Field.ALBUM] = TXXX(encoding=3, text=["Fake Album"])
    fake.tags[ID3Field.TRACKNUMBER] = TXXX(encoding=3, text=["1/10"])
    fake.tags[DefaultPlayerTags.TEXT] = TXXX(encoding=3, text=["5"])
    return fake


@pytest.fixture
def random_object():
    # Random object for testing.
    return object()


@pytest.fixture
def conflict_ratings():
    return {
        DefaultPlayerTags.TEXT.name: Rating(2.5, RatingScale.ZERO_TO_FIVE),
        DefaultPlayerTags.MEDIAMONKEY.name: Rating(4.5, RatingScale.POPM),
    }


@pytest.fixture(autouse=True)
def dummy_tag():
    # Autouse dummy AudioTag for tests that use throwaway tag values.
    return AudioTag(ID="dummy", title="Dummy", artist="Dummy", album="Dummy", track=1)


@pytest.fixture(autouse=True)
def dummy_track():
    # Autouse dummy track for tests that use throwaway track values.
    return MagicMock(ID="dummy_track")


class TestCanHandle:
    @pytest.mark.parametrize(
        "file_fixture_name, expected",
        [
            ("id3_file_with_tags", True),
            ("id3_file_without_tags", True),
            ("fake_file_with_tags", False),
            ("random_object", False),
        ],
    )
    def test_can_handle_success_or_failure_based_on_file_type(self, file_fixture_name, expected, request, handler):
        file_obj = request.getfixturevalue(file_fixture_name)
        assert handler.can_handle(file_obj) == expected


class TestExtractMetadata:
    def test_extract_metadata_success_popm_and_txxx(self, mp3_file_factory, handler):
        id3 = mp3_file_factory(rating=0.9, rating_tags=[DefaultPlayerTags.TEXT, DefaultPlayerTags.MEDIAMONKEY, "POPM:test@test"])
        tag, raw = handler.extract_metadata(id3)
        assert isinstance(tag, AudioTag)
        assert DefaultPlayerTags.TEXT.name in raw
        assert DefaultPlayerTags.MEDIAMONKEY.name in raw
        assert any(key.startswith("UNKNOWN") for key in raw)

    def test_try_normalize_success_popm_and_text(self, handler):
        r1 = handler._try_normalize(str(make_raw_rating(DefaultPlayerTags.MEDIAMONKEY, 0.8)), DefaultPlayerTags.MEDIAMONKEY.name)
        r2 = handler._try_normalize(make_raw_rating(DefaultPlayerTags.TEXT, 0.9), DefaultPlayerTags.TEXT.name)
        assert isinstance(r1, Rating)
        assert isinstance(r2, Rating)
        assert r1.to_float(RatingScale.NORMALIZED) == 0.8
        assert r2.to_float(RatingScale.NORMALIZED) == 0.9

    def test_extract_metadata_ignores_non_popm_txxx_frames(self, handler, mp3_file_factory):
        audio = mp3_file_factory()
        audio.tags["TXXX:RATING"] = COMM(encoding=3, lang="eng", desc="desc", text=["not a rating"])
        tag, raw = handler.extract_metadata(audio)
        assert "TEXT" not in raw and "TXXX:RATING" not in raw


class TestResolveRating:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"TEXT": "banana", "MEDIAMONKEY": "not_a_rating"}, Rating.unrated()),
            ({"TEXT": "", "MEDIAMONKEY": ""}, Rating.unrated()),
            ({"TEXT": None, "MEDIAMONKEY": None}, Rating.unrated()),
            ({"TEXT": "6", "MEDIAMONKEY": "260"}, Rating.unrated()),
            ({"TEXT": "-1", "MEDIAMONKEY": "-2"}, Rating.unrated()),
            ({}, Rating.unrated()),
        ],
    )
    def test_resolve_rating_ambiguous_invalid(self, handler, raw, expected):
        # All tags fail normalization (invalid, empty, None, out-of-bounds, or empty dict).
        handler.conflict_resolution_strategy = None
        rating = handler.resolve_rating(raw, dummy_tag)
        assert rating == expected

    @pytest.mark.parametrize(
        "strategy,raw,priority,expected",
        [
            # HIGHEST: should pick the highest normalized value (5.0 normalized)
            (ConflictResolutionStrategy.HIGHEST, {"TEXT": "2.0", "MEDIAMONKEY": "255"}, None, Rating(1.0, scale=RatingScale.NORMALIZED)),
            # LOWEST: should pick the lowest normalized value (2.0/5 = 0.4 normalized)
            (ConflictResolutionStrategy.LOWEST, {"TEXT": "2.0", "MEDIAMONKEY": "255"}, None, Rating(0.4)),
            # AVERAGE: average of 0.4 and 1.0 normalized
            (ConflictResolutionStrategy.AVERAGE, {"TEXT": "2.0", "MEDIAMONKEY": "255"}, None, Rating((0.4 + 1.0) / 2)),
            # PRIORITIZED_ORDER: should pick the tag in priority order (TEXT first, 2.0/5 = 0.4 normalized)
            (ConflictResolutionStrategy.PRIORITIZED_ORDER, {"TEXT": "2.0", "MEDIAMONKEY": "255"}, ["TEXT", "MEDIAMONKEY"], Rating(0.4)),
            # PRIORITIZED_ORDER: MEDIAMONKEY first (255 = 1.0 normalized)
            (ConflictResolutionStrategy.PRIORITIZED_ORDER, {"TEXT": "2.0", "MEDIAMONKEY": "255"}, ["MEDIAMONKEY", "TEXT"], Rating(1.0, scale=RatingScale.NORMALIZED)),
            # LOWEST: multi-tag (lowest is 0.0 normalized)
            (ConflictResolutionStrategy.LOWEST, {"TEXT": "1.0", "MEDIAMONKEY": "192", "WINAMP": "13"}, None, Rating(0.1)),
            # AVERAGE: multi-tag (1.0/5=0.2, 255=1.0, 0.0=0.0)
            (ConflictResolutionStrategy.AVERAGE, {"TEXT": "1.0", "MEDIAMONKEY": "255", "WINAMP": "13"}, None, Rating((0.2 + 1.0 + 0.1) / 3)),
        ],
    )
    def test_resolve_rating_conflict_strategies_param(self, handler, strategy, raw, priority, expected):
        handler.conflict_resolution_strategy = strategy
        if strategy == ConflictResolutionStrategy.PRIORITIZED_ORDER:
            handler.tag_priority_order = priority
        rating = handler.resolve_rating(raw, dummy_tag)
        assert abs(rating.to_float(RatingScale.NORMALIZED) - expected.to_float(RatingScale.NORMALIZED)) < 1e-6

    @pytest.mark.parametrize(
        "user_choice_idx,expected",
        [
            (0, Rating(0.4, scale=RatingScale.NORMALIZED)),  # Select TEXT (2.0/5 = 0.4)
            (1, Rating(1.0, scale=RatingScale.NORMALIZED)),  # Select MEDIAMONKEY (255 = 1.0)
            (2, None),  # Skip
        ],
    )
    def test_resolve_rating_conflict_choice_param(self, handler, dummy_tag, user_choice_idx, expected):
        handler.conflict_resolution_strategy = ConflictResolutionStrategy.CHOICE
        raw = {"TEXT": "2.0", "MEDIAMONKEY": "255"}
        PROMPT_RESPONSES.clear()
        # Simulate user selecting the option at user_choice_idx (or None for skip)
        if user_choice_idx < 2:
            PROMPT_RESPONSES.append(list(raw.keys())[user_choice_idx])
        else:
            PROMPT_RESPONSES.append(None)
        rating = handler.resolve_rating(raw, dummy_tag)
        if expected is None:
            assert rating is None
        else:
            assert abs(rating.to_float(RatingScale.NORMALIZED) - expected.to_float(RatingScale.NORMALIZED)) < 1e-6

    def test_resolve_rating_unknown_tag_raises(self, handler):
        # Unknown tag key should raise ValueError.
        raw = {"UNKNOWN_TAG": "4.0"}
        with pytest.raises(ValueError, match="Unknown tag_key 'UNKNOWN_TAG'."):
            handler.resolve_rating(raw, dummy_tag)

    def test_resolve_rating_failure_unknown_strategy_fallback(self, handler):
        handler.conflict_resolution_strategy = "UNSUPPORTED_STRATEGY"
        ratings = {"TEXT": Rating(0.2), "MEDIAMONKEY": Rating(0.4)}
        result = handler._resolve_conflict(ratings, dummy_track)
        expected = handler._resolve_highest(ratings, dummy_track)
        assert result == expected

    def test_resolve_rating_equivalent_representations(self, handler):
        # Different representations of the same value should normalize and match.
        raw = {"TEXT": "5", "MEDIAMONKEY": "255", "POPM:test@test": "255"}
        rating = handler.resolve_rating(raw, dummy_tag)
        assert rating.to_float(RatingScale.ZERO_TO_FIVE) == 5.0


class TestApplyTags:
    def test_audio_without_tags_success(self, handler, mp3_file_factory):
        audio = mp3_file_factory()
        audio.tags = {}
        expected_rating = Rating(4.5)
        handler.apply_tags(audio, None, expected_rating)
        assert audio.tags[DefaultPlayerTags.MEDIAMONKEY].rating == expected_rating.to_int(RatingScale.POPM)

    def test_apply_tags_success_metadata_only(self, handler, mp3_file_factory):
        audio = mp3_file_factory()
        popm_email = get_popm_email(DefaultPlayerTags.MEDIAMONKEY)
        audio.tags[DefaultPlayerTags.MEDIAMONKEY] = POPM(email=popm_email, rating=196, count=0)

        original_rating = audio.tags[DefaultPlayerTags.MEDIAMONKEY].rating

        tag = AudioTag(ID="error2", title="Test Title", artist="Test Artist", album="Test Album", track=5)

        handler.apply_tags(audio, tag, None)

        assert audio.tags[ID3Field.TITLE].text[0] == "Test Title"
        assert audio.tags[ID3Field.ARTIST].text[0] == "Test Artist"
        assert audio.tags[ID3Field.ALBUM].text[0] == "Test Album"
        assert audio.tags[ID3Field.TRACKNUMBER].text[0] == "5"
        assert audio.tags[DefaultPlayerTags.MEDIAMONKEY].rating == original_rating

    def test_apply_tags_success_rating_only(self, handler, mp3_file_factory):
        audio = mp3_file_factory(rating=0.6, rating_tags=[DefaultPlayerTags.TEXT, DefaultPlayerTags.MEDIAMONKEY])

        original_title = audio.tags.get(ID3Field.TITLE)
        original_artist = audio.tags.get(ID3Field.ARTIST)
        original_album = audio.tags.get(ID3Field.ALBUM)
        original_tracknumber = audio.tags.get(ID3Field.TRACKNUMBER).text[0]
        tag = AudioTag(ID="error3", title=None, artist=None, album=None, track=None)

        expected_rating = Rating(4.5)
        handler.apply_tags(audio, tag, expected_rating)

        assert any(k.startswith("POPM:") for k in audio.tags)
        assert audio.tags[DefaultPlayerTags.MEDIAMONKEY].rating == expected_rating.to_int(RatingScale.POPM)
        assert audio.tags.get(ID3Field.TITLE).text[0] == original_title
        assert audio.tags.get(ID3Field.ARTIST).text[0] == original_artist
        assert audio.tags.get(ID3Field.ALBUM).text[0] == original_album
        assert audio.tags.get(ID3Field.TRACKNUMBER).text[0] == original_tracknumber

    def test_apply_tags_write_all_no_tags(self, handler, mp3_file_factory):
        handler.tag_write_strategy = TagWriteStrategy.WRITE_ALL
        handler.discovered_rating_tags = {}

        rating = Rating(0.5, RatingScale.NORMALIZED)
        audio = mp3_file_factory(rating=rating)

        tag = AudioTag(ID="test", title="A")

        handler.apply_tags(audio, tag, Rating(4.5))

        assert audio.tags[DefaultPlayerTags.MEDIAMONKEY].rating == rating.to_int(RatingScale.POPM)
        assert audio.tags[DefaultPlayerTags.TEXT].text[0] == rating.to_str(RatingScale.ZERO_TO_FIVE)
        assert audio.tags.get(ID3Field.TITLE).text[0] == "A"

    @pytest.mark.parametrize(
        "write_strategy, expected_tags",
        [
            (TagWriteStrategy.OVERWRITE_DEFAULT, {DefaultPlayerTags.MEDIAMONKEY: 0.8}),
            (TagWriteStrategy.WRITE_ALL, {DefaultPlayerTags.MEDIAMONKEY: 0.8, DefaultPlayerTags.TEXT: 0.8, DefaultPlayerTags.WINAMP: 0.8}),
            (TagWriteStrategy.WRITE_DEFAULT, {DefaultPlayerTags.MEDIAMONKEY: 0.8, DefaultPlayerTags.TEXT: 0.6, DefaultPlayerTags.WINAMP: 0.6}),
            (None, {DefaultPlayerTags.TEXT: 0.6, DefaultPlayerTags.WINAMP: 0.6}),
        ],
    )
    def test_apply_tags_success_expected_tags(self, write_strategy, expected_tags, handler, mp3_file_factory):
        handler.tag_write_strategy = write_strategy

        all_tags = [DefaultPlayerTags.TEXT, DefaultPlayerTags.MEDIAMONKEY, DefaultPlayerTags.WINAMP]
        handler.discovered_rating_tags = {tag.name for tag in all_tags}
        audio = mp3_file_factory(rating=0.6, rating_tags=[DefaultPlayerTags.TEXT, DefaultPlayerTags.WINAMP])

        assert DefaultPlayerTags.MEDIAMONKEY not in audio.tags

        handler.apply_tags(audio, None, Rating(4))
        for tag in all_tags:
            frame = audio.tags.get(tag)

            if tag in expected_tags:
                expected = Rating(expected_tags[tag])
                assert frame is not None, f"{tag} should exist"
                if tag == "TXXX:RATING":
                    assert isinstance(frame, TXXX)
                    assert frame.text[0] == expected.to_str(RatingScale.ZERO_TO_FIVE)
                else:
                    assert isinstance(frame, POPM)
                    assert frame.rating == expected.to_float(RatingScale.POPM)
            else:
                assert frame is None, f"{tag} should NOT exist"

    def test_remove_existing_id3_tags_success(self, handler, mp3_file_factory):
        audio = mp3_file_factory()
        assert DefaultPlayerTags.TEXT in audio.tags
        assert DefaultPlayerTags.MEDIAMONKEY in audio.tags

        handler._remove_existing_id3_tags(audio)

        assert DefaultPlayerTags.TEXT not in audio.tags
        assert DefaultPlayerTags.MEDIAMONKEY not in audio.tags


class TestReadTags:
    def test_read_tags_success_returns_raw_ratings(self, handler, mp3_file_factory):
        handler.conflict_resolution_strategy = None
        audio = mp3_file_factory(rating_tags=[DefaultPlayerTags.TEXT, DefaultPlayerTags.MEDIAMONKEY])
        audio = add_or_update_id3frame(audio, rating=0.2, rating_tags=DefaultPlayerTags.TEXT)
        audio = add_or_update_id3frame(audio, rating=0.8, rating_tags=DefaultPlayerTags.MEDIAMONKEY)

        track, raw = handler.read_tags(audio)

        assert isinstance(track, AudioTag)
        assert raw is not None
        assert DefaultPlayerTags.TEXT.name in raw or DefaultPlayerTags.MEDIAMONKEY.name in raw

    def test_read_tags_success_no_conflicts(self, handler, mp3_file_factory):
        audio = mp3_file_factory(rating=0.3, rating_tags=[DefaultPlayerTags.TEXT, DefaultPlayerTags.MEDIAMONKEY])

        track, raw = handler.read_tags(audio)

        assert isinstance(track, AudioTag)
        assert raw is None


class TestResolveChoice:
    def test_resolve_choice_select_rating_success(self, handler, conflict_ratings):
        track = AudioTag(ID="conflict", title="Test Track", artist="Artist", album="Album", track=1)
        items = list(conflict_ratings.items())
        player_key, expected_rating = items[0]
        PROMPT_RESPONSES.clear()
        PROMPT_RESPONSES.append(player_key)
        result = handler._resolve_choice(conflict_ratings, track)
        assert isinstance(result, Rating)
        assert result == expected_rating

    def test_resolve_choice_select_skip_success(self, handler, conflict_ratings):
        track = AudioTag(ID="conflict", title="Test Track", artist="Artist", album="Album", track=1)
        PROMPT_RESPONSES.clear()
        PROMPT_RESPONSES.append(None)
        result = handler._resolve_choice(conflict_ratings, track)
        assert result is None

    def test_resolve_choice_order_matches_items(self, handler, conflict_ratings):
        track = AudioTag(ID="conflict", title="Test Track", artist="Artist", album="Album", track=1)
        PROMPT_RESPONSES.clear()
        PROMPT_RESPONSES.append(next(iter(conflict_ratings.keys())))
        result = handler._resolve_choice(conflict_ratings, track)
        assert result is not None  # Should return first rating option


class TestResolvePrioritizedOrder:
    def test_raises_if_tag_priority_order_none(self, handler):
        handler.tag_priority_order = None
        ratings_by_tag = {"TEXT": Rating(3.0)}
        handler.logger = MagicMock()
        with pytest.raises(ValueError, match="No tag_priority_order for PRIORITIZED_ORDER"):
            handler._resolve_prioritized_order(ratings_by_tag, dummy_tag)
        handler.logger.warning.assert_called_once_with("No tag_priority_order for PRIORITIZED_ORDER")

    def test_returns_unrated_if_no_keys_match(self, handler):
        handler.tag_priority_order = ["TEXT", "MEDIAMONKEY"]
        ratings_by_tag = {"WINAMP": Rating(2.0)}
        result = handler._resolve_prioritized_order(ratings_by_tag, dummy_tag)
        assert isinstance(result, Rating)
        assert result == Rating.unrated()

    def test_returns_first_matching_rating(self, handler):
        handler.tag_priority_order = ["TEXT", "MEDIAMONKEY"]
        ratings_by_tag = {"TEXT": Rating(4.0), "MEDIAMONKEY": Rating(2.0)}
        result = handler._resolve_prioritized_order(ratings_by_tag, dummy_tag)
        assert result == ratings_by_tag["TEXT"]

    def test_returns_second_if_first_missing(self, handler):
        handler.tag_priority_order = ["TEXT", "MEDIAMONKEY"]
        ratings_by_tag = {"MEDIAMONKEY": Rating(2.0)}
        result = handler._resolve_prioritized_order(ratings_by_tag, dummy_tag)
        assert result == ratings_by_tag["MEDIAMONKEY"]


@pytest.fixture
def mock_finalize_strategy_deps(request, handler, monkeypatch):
    """Mock dependencies for finalize_rating_strategy tests."""
    config = request.param if hasattr(request, "param") else {}

    for key in ("conflict_resolution_strategy", "tag_write_strategy", "default_tag", "tag_priority_order"):
        if key in config:
            setattr(handler, key, config[key])

    num_from_handler = config.get("conflicts_from_handler", 2)
    num_from_others = config.get("conflicts_from_others", 1)

    handler.conflicts = []
    for i in range(num_from_handler):
        handler.conflicts.append({"handler": handler, "track": AudioTag(ID=f"own{i}", title="Test", artist="Test", album="Test", track=1)})
    for i in range(num_from_others):
        other = MagicMock()
        handler.conflicts.append({"handler": other, "track": AudioTag(ID=f"other{i}", title="Test", artist="Test", album="Test", track=1)})

    handler.discovered_rating_tags = {"TEXT", "MEDIAMONKEY"}
    handler.stats_mgr.get.return_value = config.get("tag_counts", {"TEXT": 5, "MEDIAMONKEY": 3})

    handler._print_summary = MagicMock()
    handler._show_conflicts = MagicMock()
    handler.prompt = DummyPrompt()

    handler.cfg = MagicMock()
    monkeypatch.setattr("filesystem_provider.get_manager", lambda: MagicMock(get_config_manager=lambda: handler.cfg))

    return handler


class TestFinalizeRatingStrategy:
    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps",
        [
            {
                "tag_write_strategy": TagWriteStrategy.WRITE_ALL,
                "conflicts_from_handler": 2,
                "conflicts_from_others": 1,
                "tag_counts": {"TEXT": 5, "MM": 3},
                "discovered_rating_tags": {"TEXT", "MM"},
            },
            {
                "conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST,
                "conflicts_from_handler": 0,
                "conflicts_from_others": 0,
                "tag_counts": {"TEXT": 5},
                "discovered_rating_tags": {"TEXT"},
            },
            {
                "conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST,
                "conflicts_from_handler": 1,
                "conflicts_from_others": 1,
                "tag_counts": {"TEXT": 5},
                "discovered_rating_tags": {"TEXT", "MEDIAMONKEY"},
            },
            {
                "conflict_resolution_strategy": ConflictResolutionStrategy.PRIORITIZED_ORDER,
                "tag_priority_order": ["TEXT"],
                "conflicts_from_handler": 2,
                "conflicts_from_others": 1,
                "tag_counts": {"TEXT": 5, "MM": 3},
                "discovered_rating_tags": {"TEXT", "MM"},
            },
            {
                "conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST,
                "conflicts_from_handler": 1,
                "conflicts_from_others": 1,
                "tag_counts": {"TEXT": 5},
                "discovered_rating_tags": {"TEXT"},
            },
            {
                "conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST,
                "tag_write_strategy": TagWriteStrategy.WRITE_ALL,
                "conflicts_from_handler": 2,
                "conflicts_from_others": 1,
                "tag_counts": {"TEXT": 5, "MM": 3},
                "discovered_rating_tags": {"TEXT", "MM"},
            },
            {
                "tag_write_strategy": TagWriteStrategy.WRITE_ALL,
                "conflicts_from_handler": 2,
                "conflicts_from_others": 1,
                "tag_counts": {"TEXT": 5, "MM": 3},
                "discovered_rating_tags": {"TEXT", "MM"},
            },
            {
                "tag_write_strategy": TagWriteStrategy.WRITE_DEFAULT,
                "default_tag": "TEXT",
                "conflicts_from_handler": 2,
                "conflicts_from_others": 1,
                "tag_counts": {"TEXT": 5, "MM": 3},
                "discovered_rating_tags": {"TEXT", "MM"},
            },
            {
                "tag_write_strategy": TagWriteStrategy.OVERWRITE_DEFAULT,
                "default_tag": "TEXT",
                "conflicts_from_handler": 2,
                "conflicts_from_others": 1,
                "tag_counts": {"TEXT": 5, "MM": 3},
                "discovered_rating_tags": {"TEXT", "MM"},
            },
        ],
        indirect=["mock_finalize_strategy_deps"],
    )
    def test_finalize_rating_strategy_early_exit_conditions(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps
        PROMPT_RESPONSES.clear()
        handler.finalize_rating_strategy(handler.conflicts)
        handler._show_conflicts.assert_not_called()

    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps",
        [{"conflict_resolution_strategy": ConflictResolutionStrategy.PRIORITIZED_ORDER, "tag_priority_order": ["TEXT"], "tag_write_strategy": None}],
        indirect=True,
    )
    def test_tag_priority_prompt_skipped_if_pre_set(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps

        PROMPT_RESPONSES.clear()
        # Always select the first option and answer yes
        PROMPT_RESPONSES.append(None)  # For tag_priority_order prompt, but it's skipped
        PROMPT_RESPONSES.append(True)
        handler.finalize_rating_strategy(handler.conflicts)
        # Confirm summary was printed (side effect)
        assert handler._print_summary.called

    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps",
        [{"conflict_resolution_strategy": ConflictResolutionStrategy.PRIORITIZED_ORDER, "tag_priority_order": None}],
        indirect=True,
    )
    def test_tag_priority_prompt_sets_value(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps

        # Simulate user selecting the first two tag keys (not labels) for tag_priority_order, then answer yes
        PROMPT_RESPONSES[:] = [["TEXT", "MEDIAMONKEY"], True]
        handler.finalize_rating_strategy(handler.conflicts)
        # Confirm config was saved (side effect)
        handler.cfg.save_config.assert_called_once()

    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps",
        [{"conflict_resolution_strategy": None, "tag_write_strategy": TagWriteStrategy.WRITE_ALL}],
        indirect=True,
    )
    def test_write_strategy_prompt_skipped_if_pre_set(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps

        PROMPT_RESPONSES.clear()
        PROMPT_RESPONSES.append(ConflictResolutionStrategy.HIGHEST.display)
        PROMPT_RESPONSES.append(True)
        handler.finalize_rating_strategy(handler.conflicts)
        # Confirm summary was printed and config was not changed (side effect)
        assert handler._print_summary.called

    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps",
        [
            {"tag_write_strategy": TagWriteStrategy.WRITE_ALL},
            {"conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST, "tag_write_strategy": TagWriteStrategy.WRITE_DEFAULT, "default_tag": "TEXT"},
            {"conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST, "tag_write_strategy": TagWriteStrategy.OVERWRITE_DEFAULT, "default_tag": "TEXT"},
        ],
        indirect=True,
    )
    def test_default_tag_prompt_skipped_if_not_required(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps
        PROMPT_RESPONSES.clear()
        PROMPT_RESPONSES.append(None)  # For default_tag prompt, but it's skipped
        PROMPT_RESPONSES.append(True)
        handler.finalize_rating_strategy(handler.conflicts)
        # Confirm config and state are as expected
        if handler.conflict_resolution_strategy is None:
            assert handler.cfg.save_config.call_count in (0, 1)
        else:
            assert handler.cfg.save_config.call_count == 0

    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps,user_choice_display,expected",
        [
            ({"conflict_resolution_strategy": None}, "Highest", ConflictResolutionStrategy.HIGHEST),
            ({"conflict_resolution_strategy": None}, "Lowest", ConflictResolutionStrategy.LOWEST),
            ({"conflict_resolution_strategy": None}, "Average", ConflictResolutionStrategy.AVERAGE),
        ],
        indirect=["mock_finalize_strategy_deps"],
    )
    def test_conflict_strategy_prompt_sets_value(self, mock_finalize_strategy_deps, user_choice_display, expected):
        handler = mock_finalize_strategy_deps
        PROMPT_RESPONSES.clear()
        PROMPT_RESPONSES.append(user_choice_display)
        PROMPT_RESPONSES.append(True)
        handler.finalize_rating_strategy(handler.conflicts)
        assert handler.conflict_resolution_strategy == expected
        handler.cfg.save_config.assert_called_once()

    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps,user_choice_display,expected",
        [
            ({"tag_write_strategy": None}, "Write All", TagWriteStrategy.WRITE_ALL),
            ({"tag_write_strategy": None}, "Write Default", TagWriteStrategy.WRITE_DEFAULT),
            ({"tag_write_strategy": None}, "Overwrite Default", TagWriteStrategy.OVERWRITE_DEFAULT),
        ],
        indirect=["mock_finalize_strategy_deps"],
    )
    def test_write_strategy_prompt_sets_value(self, mock_finalize_strategy_deps, user_choice_display, expected):
        handler = mock_finalize_strategy_deps
        PROMPT_RESPONSES[:] = [expected, True]
        handler.finalize_rating_strategy(handler.conflicts)
        assert handler.tag_write_strategy == expected
        handler.cfg.save_config.assert_called_once()

    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps",
        [{"conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST, "tag_write_strategy": TagWriteStrategy.WRITE_DEFAULT, "default_tag": None}],
        indirect=True,
    )
    def test_default_tag_prompt_sets_value(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps
        PROMPT_RESPONSES[:] = ["Text", True]
        handler.finalize_rating_strategy(handler.conflicts)
        assert handler.default_tag == "Text"
        handler.cfg.save_config.assert_called_once()

    @pytest.mark.parametrize("mock_finalize_strategy_deps", [{"conflict_resolution_strategy": None}], indirect=True)
    def test_user_declines_config_save(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps

        PROMPT_RESPONSES[:] = [ConflictResolutionStrategy.HIGHEST.display, False]
        handler.finalize_rating_strategy(handler.conflicts)
        handler.cfg.save_config.assert_not_called()

    @pytest.mark.parametrize("mock_finalize_strategy_deps", [{"conflict_resolution_strategy": None}], indirect=True)
    def test_show_conflicts_triggers_reprompt(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps
        PROMPT_RESPONSES[:] = ["show_conflicts", ConflictResolutionStrategy.HIGHEST, True]
        handler.finalize_rating_strategy(handler.conflicts)
        handler._show_conflicts.assert_called_once()
        assert handler.conflict_resolution_strategy == ConflictResolutionStrategy.HIGHEST
        handler.cfg.save_config.assert_called_once()

    @pytest.mark.parametrize(
        "mock_finalize_strategy_deps",
        [
            {"conflict_resolution_strategy": ConflictResolutionStrategy.HIGHEST, "tag_write_strategy": TagWriteStrategy.WRITE_DEFAULT, "default_tag": "TEXT"},
        ],
        indirect=True,
    )
    def test_finalize_rating_strategy_no_save_when_no_change(self, mock_finalize_strategy_deps):
        handler = mock_finalize_strategy_deps
        PROMPT_RESPONSES.clear()
        PROMPT_RESPONSES.append(None)
        handler.finalize_rating_strategy(handler.conflicts)
        handler.cfg.save_config.assert_not_called()


class TestApplyRating:
    def test_apply_rating_adds_txxx_rating(self, handler, mp3_file_factory):
        handler.tag_registry.get_id3_tag_for_key = lambda key: "TXXX:RATING"

        audio = mp3_file_factory()
        if "TXXX:RATING" in audio.tags:
            del audio.tags["TXXX:RATING"]
        assert "TXXX:RATING" not in audio.tags

        result = handler._apply_rating(audio, Rating(4.0), {"TEXT"})
        frame = audio.tags.get("TXXX:RATING")
        assert isinstance(frame, TXXX)
        assert frame.text[0] == "4"
        assert result is audio

    def test_apply_rating_updates_existing_txxx_rating(self, handler, mp3_file_factory):
        handler.tag_registry.get_id3_tag_for_key = lambda key: "TXXX:RATING"

        audio = mp3_file_factory()
        add_or_update_id3frame(audio, rating=0.4, rating_tags=["TEXT"])

        result = handler._apply_rating(audio, Rating(3.0), {"TEXT"})
        frame = audio.tags.get("TXXX:RATING")
        assert isinstance(frame, TXXX)
        assert frame.text[0] == "3"
        assert result is audio

    def test_apply_rating_adds_popm(self, handler, mp3_file_factory):
        handler.tag_registry.get_id3_tag_for_key = lambda key: "POPM:test@test"
        handler.tag_registry.get_popm_email_for_key = lambda key: "test@test"

        audio = mp3_file_factory()
        assert "POPM:test@test" not in audio.tags

        result = handler._apply_rating(audio, Rating(1.0, scale=RatingScale.NORMALIZED), {"POPM"})
        frame = audio.tags.get("POPM:test@test")
        assert isinstance(frame, POPM)
        assert frame.rating == 255
        assert result is audio

    def test_apply_rating_updates_existing_popm(self, handler, mp3_file_factory):
        handler.tag_registry.get_id3_tag_for_key = lambda key: "POPM:test@test"
        handler.tag_registry.get_popm_email_for_key = lambda key: "test@test"

        audio = mp3_file_factory()
        add_or_update_id3frame(audio, rating=0.5, rating_tags=["POPM:test@test"])

        result = handler._apply_rating(audio, Rating(1.0, scale=RatingScale.NORMALIZED), {"POPM"})
        frame = audio.tags.get("POPM:test@test")
        assert isinstance(frame, POPM)
        assert frame.rating == 255
        assert result is audio

    def test_apply_rating_unknown_tag_warns_and_skips(self, handler, mp3_file_factory):
        handler.tag_registry.get_id3_tag_for_key = lambda key: None

        audio = mp3_file_factory()

        result = handler._apply_rating(audio, Rating(3.5), {"UNKNOWN"})
        assert result is audio
        assert "UNKNOWN" not in audio.tags
        handler.logger.warning.assert_called_once()

    def test_apply_rating_needs_save_false_non_popm(self, handler, mp3_file_factory):
        handler.tag_registry.get_id3_tag_for_key = lambda key: "TXXX:RATING"
        audio = mp3_file_factory()
        audio.tags["TXXX:RATING"].text = ["4"]
        result = handler._apply_rating(audio, Rating(4.0), {"TEXT"})
        assert audio.tags["TXXX:RATING"].text[0] == "4"
        assert result is audio
