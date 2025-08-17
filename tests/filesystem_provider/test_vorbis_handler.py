import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mutagen.flac import FLAC

from filesystem_provider import VorbisField, VorbisHandler
from manager.config_manager import ConflictResolutionStrategy
from ratings import Rating, RatingScale
from sync_items import AudioTag
from tests.helpers import make_raw_rating


@pytest.fixture
def handler():
    handler = VorbisHandler()
    handler.logger = MagicMock()
    handler.stats_mgr = MagicMock()
    return handler


@pytest.fixture
def vorbis_file_factory():
    test_flac_path = Path("tests/test.flac")

    def _factory(fmps_rating: float = 1.0, rating: float = 1.0, fmps_scale: RatingScale = RatingScale.NORMALIZED, rating_scale: RatingScale = RatingScale.ZERO_TO_FIVE, **kwargs):
        fd, temp_path = tempfile.mkstemp(suffix=".flac")
        temp_path = Path(temp_path)

        shutil.copyfile(test_flac_path, temp_path)

        audio = FLAC(temp_path)
        audio.save = MagicMock(side_effect=lambda *args, **kw: audio.save())

        # Default metadata
        audio[VorbisField.TITLE.value] = [kwargs.get("title", "Default Title")]
        audio[VorbisField.ARTIST.value] = [kwargs.get("artist", "Default Artist")]
        audio[VorbisField.ALBUM.value] = [kwargs.get("album", "Default Album")]
        audio[VorbisField.TRACKNUMBER.value] = [kwargs.get("track", "1")]

        if fmps_rating is not None:
            audio[VorbisField.FMPS_RATING] = Rating(fmps_rating, fmps_scale).to_str()
        if rating is not None:
            audio[VorbisField.RATING] = Rating(rating, rating_scale).to_str()

        return audio

    return _factory


@pytest.fixture
def vorbis_file_with_fmps_rating(vorbis_file_factory):
    return vorbis_file_factory(fmps_rating=0.8)


@pytest.fixture
def vorbis_file_with_standard_rating(vorbis_file_factory):
    return vorbis_file_factory(rating=4.5)


@pytest.fixture
def vorbis_file_without_ratings(vorbis_file_factory):
    return vorbis_file_factory(fmps_rating=None, rating=None)


@pytest.fixture
def fake_object_with_tags():
    fake = MagicMock()
    fake.tags = {"TITLE": ["Fake Title"], "ARTIST": ["Fake Artist"]}
    return fake


class TestCanHandle:
    @pytest.mark.parametrize(
        "file_fixture_name, expected",
        [
            ("vorbis_file_with_fmps_rating", True),
            ("vorbis_file_with_standard_rating", True),
            ("vorbis_file_without_ratings", True),
            ("fake_object_with_tags", False),
        ],
    )
    def test_can_handle_success_or_failure(self, file_fixture_name, expected, request, handler):
        file_obj = request.getfixturevalue(file_fixture_name)
        assert handler.can_handle(file_obj) == expected


class TestExtractMetadata:
    def test_extract_metadata_success(self, vorbis_file_factory, handler):
        file = vorbis_file_factory()
        tag, raw = handler.extract_metadata(file)
        assert isinstance(tag, AudioTag)
        assert "FMPS_RATING" in raw
        assert "RATING" in raw

    def test_extract_metadata_empty_when_no_rating_keys(self, handler):
        """Returns empty raw dict when no rating keys are present."""
        audio_file = MagicMock()
        audio_file.tags = {}
        audio_file.filename = "dummy.flac"
        audio_file.get = MagicMock()

        tag, raw = handler.extract_metadata(audio_file)
        assert raw == {}

    def test_get_audiotag_handles_missing_fields(self, handler):
        audio = MagicMock()
        audio.get.side_effect = lambda key, default=None: default if key != VorbisField.TRACKNUMBER else ["2/10"]
        audio.info = None
        tag = handler._get_audiotag(audio, "test.flac")
        assert tag.title == ""
        assert tag.artist == ""
        assert tag.album == ""
        assert tag.track == 2
        assert tag.duration == -1


class TestTryNormalize:
    @pytest.mark.parametrize(
        "value, field",
        [("invalid", VorbisField.FMPS_RATING), ("invalid", VorbisField.RATING)],
    )
    def test_try_normalize_returns_unrated_if_aggressive_enabled(self, value, field, handler):
        handler.aggressive_inference = True
        result = handler._try_normalize(value, field.value)
        assert isinstance(result, Rating)
        assert result.is_unrated

    @pytest.mark.parametrize(
        "value, field",
        [("invalid", VorbisField.FMPS_RATING), ("invalid", VorbisField.RATING)],
    )
    def test_try_normalize_returns_none_if_aggressive_disabled(self, value, field, handler):
        handler.aggressive_inference = False
        result = handler._try_normalize(value, field.value)
        assert result is None


class TestResolveRating:
    def test_conflict_resolution_highest(self, handler):
        tag = AudioTag(ID="z", title="T", artist="A", album="B", track=1)
        raw = {
            VorbisField.FMPS_RATING.value: make_raw_rating("FMPS_RATING", 0.7, rating_scale=RatingScale.NORMALIZED),
            VorbisField.RATING.value: make_raw_rating("RATING", 0.9, rating_scale=RatingScale.ZERO_TO_FIVE),
        }
        resolved = handler.resolve_rating(raw, tag)
        assert isinstance(resolved, Rating)
        assert round(resolved.to_float(RatingScale.ZERO_TO_FIVE), 1) == 4.5

    @pytest.mark.parametrize(
        "aggressive,raw,expected,desc",
        [
            (False, {VorbisField.FMPS_RATING.value: "invalid", VorbisField.RATING.value: "invalid"}, Rating.unrated(), "all fail normalization"),
            (False, {VorbisField.FMPS_RATING.value: "0.8", VorbisField.RATING.value: "invalid"}, None, "some succeed, some fail (ambiguous)"),
            (False, {VorbisField.FMPS_RATING.value: "0.8", VorbisField.RATING.value: "4.0"}, Rating(0.8), "all succeed, all equal"),
            (False, {VorbisField.FMPS_RATING.value: "0.7", VorbisField.RATING.value: "4.5"}, None, "all succeed, conflict (strategy applies)"),
        ],
    )
    def test_resolve_rating_ambiguous(self, handler, aggressive, raw, expected, desc):
        """Covers all resolve_rating branches for VorbisHandler."""
        handler.aggressive_inference = aggressive
        tag = AudioTag(ID="z", title="T", artist="A", album="B", track=1)
        if desc == "all succeed, conflict (strategy applies)":
            # Should resolve by strategy (default HIGHEST)
            result = handler.resolve_rating(raw, tag)
            assert isinstance(result, Rating)
            # Should be the highest normalized value
            vals = [Rating.try_create(v) for v in raw.values()]
            max_val = max(r.to_float(RatingScale.NORMALIZED) for r in vals if r)
            assert abs(result.to_float(RatingScale.NORMALIZED) - max_val) < 1e-6
        elif expected is None:
            # Ambiguous branch
            result = handler.resolve_rating(raw, tag)
            assert result is None
        else:
            result = handler.resolve_rating(raw, tag)
            assert isinstance(result, Rating)
            assert result == expected


class TestFinalizeRatingStrategy:
    def test_finalize_sets_inferred_scales(self, handler):
        handler.stats_mgr.get.side_effect = [
            {"NORMALIZED": 3, "ZERO_TO_FIVE": 1},
            {"ZERO_TO_FIVE": 4, "NORMALIZED": 4},  # tie, picks NORMALIZED
        ]

        conflicts = [{"handler": handler}, {"handler": handler}]
        handler.finalize_rating_strategy(conflicts)

        assert handler.fmps_rating_scale == RatingScale.NORMALIZED
        assert handler.rating_scale == RatingScale.NORMALIZED
        assert handler.aggressive_inference is True

    def test_finalize_no_stats_sets_default_scale(self, handler):
        handler.stats_mgr.get.return_value = {}
        handler.finalize_rating_strategy([{"handler": handler}])
        assert handler.fmps_rating_scale is None
        assert handler.rating_scale == RatingScale.ZERO_TO_FIVE

    def test_finalize_strategy_skips_with_no_owned_conflicts_sets_default_scale(self, handler):
        other_handler = MagicMock()
        conflicts = [{"handler": other_handler}, {"handler": other_handler}]

        handler.stats_mgr.get.return_value = {}  # Prevent crash in pick_scale()

        handler.finalize_rating_strategy(conflicts)

        assert handler.fmps_rating_scale is None
        assert handler.rating_scale == RatingScale.ZERO_TO_FIVE


class TestApplyTags:
    @pytest.mark.parametrize(
        "set_fmps,set_rating",
        [
            (True, True),
            (True, False),
            (False, True),
        ],
    )
    def test_apply_tags_writes_to_expected_fields(self, handler, vorbis_file_factory, set_fmps, set_rating):
        handler.fmps_rating_scale = RatingScale.NORMALIZED if set_fmps else None
        handler.rating_scale = RatingScale.ZERO_TO_FIVE if set_rating else None

        file = vorbis_file_factory(fmps_rating=None, rating=None)
        rating = Rating(0.6)

        result = handler.apply_tags(file, None, rating)

        if set_fmps:
            assert result["FMPS_RATING"] == [rating.to_str(RatingScale.NORMALIZED)]
        else:
            assert "FMPS_RATING" not in result

        if set_rating:
            assert result["RATING"] == [rating.to_str(RatingScale.ZERO_TO_FIVE)]
        else:
            assert "RATING" not in result

    def test_apply_tags_sets_metadata_fields(self, handler, vorbis_file_factory):
        audio = vorbis_file_factory(fmps_rating=None, standard_rating=None)
        tag = AudioTag(ID="test", title="T", artist="A", album="B", track=7)
        result = handler.apply_tags(audio, tag, None)
        assert result["TITLE"] == ["T"]
        assert result["ARTIST"] == ["A"]
        assert result["ALBUM"] == ["B"]
        assert result["TRACKNUMBER"] == ["7"]

    @pytest.mark.parametrize(
        "field, falsy_value, expected_fields",
        [
            ("title", "", ["ARTIST", "ALBUM", "TRACKNUMBER"]),
            ("artist", "", ["TITLE", "ALBUM", "TRACKNUMBER"]),
            ("album", "", ["TITLE", "ARTIST", "TRACKNUMBER"]),
            ("track", None, ["TITLE", "ARTIST", "ALBUM"]),
        ],
    )
    def test_apply_tags_skips_field_when_falsy(self, handler, vorbis_file_factory, field, falsy_value, expected_fields):
        """Skips setting a field when its value is falsy."""
        tag_kwargs = {
            "ID": "test",
            "title": "T",
            "artist": "A",
            "album": "B",
            "track": 7,
        }
        tag_kwargs[field] = falsy_value
        tag = AudioTag(**tag_kwargs)
        audio = vorbis_file_factory(fmps_rating=None, rating=None)
        field_map = {"title": "TITLE", "artist": "ARTIST", "album": "ALBUM", "track": "TRACKNUMBER"}
        field_key = field_map[field]
        if field_key in audio:
            del audio[field_key]
        result = handler.apply_tags(audio, tag, None)
        assert field_key not in result
        for ef in expected_fields:
            assert ef in result


class TestIsStrategySupported:
    def test_prioritized_order_not_supported(self, handler):
        assert not handler.is_strategy_supported(ConflictResolutionStrategy.PRIORITIZED_ORDER)
