import pytest

from ratings import Rating, RatingScale


class TestRatingInitialization:
    def test_valid_initialization(self):
        """Test initialization with valid raw and scale values."""
        r = Rating(5.0, scale=RatingScale.ZERO_TO_FIVE)
        assert r.raw == 5.0
        assert r.scale == RatingScale.ZERO_TO_FIVE
        assert abs(r._normalized - 1.0) < 1e-6

    @pytest.mark.parametrize(
        "raw,scale,aggressive,expected_scale,expected_norm",
        [
            (255, RatingScale.POPM, False, RatingScale.POPM, 1.0),
            (0.5, RatingScale.ZERO_TO_FIVE, False, RatingScale.ZERO_TO_FIVE, 0.1),
            (50, RatingScale.ZERO_TO_HUNDRED, False, RatingScale.ZERO_TO_HUNDRED, 0.5),
            (0.8, None, True, RatingScale.NORMALIZED, 0.8),
        ],
    )
    def test_initialization_param(self, raw, scale, aggressive, expected_scale, expected_norm):
        """Test initialization with various raw, scale, and aggressive combinations."""
        r = Rating(raw, scale=scale, aggressive=aggressive)
        assert r.scale == expected_scale
        assert abs(r._normalized - expected_norm) < 0.01

    def test_initialization_infer_fail(self):
        """Test initialization fails when scale cannot be inferred."""
        with pytest.raises(ValueError):
            Rating("not_a_rating")
        with pytest.raises(ValueError):
            Rating(7.5)  # Not in any scale, not aggressive

    @pytest.mark.parametrize(
        "raw,scale",
        [
            (-1, RatingScale.ZERO_TO_FIVE),
            (6, RatingScale.ZERO_TO_FIVE),
            (300, RatingScale.POPM),
            (-10, RatingScale.ZERO_TO_HUNDRED),
        ],
    )
    def test_initialization_out_of_bounds(self, raw, scale):
        """Test initialization fails when normalization is out of bounds."""
        with pytest.raises(ValueError, match="Invalid POPM byte value|Normalized rating out of bounds"):
            Rating(raw, scale=scale)


class TestRatingConversion:
    @pytest.mark.parametrize(
        "value,scale,expected",
        [
            (1.0, RatingScale.ZERO_TO_FIVE, "1"),
            (0.5, RatingScale.ZERO_TO_FIVE, "0.5"),
            (255, RatingScale.POPM, "255"),
            (128, RatingScale.POPM, "128"),
        ],
    )
    def test_to_str(self, value, scale, expected):
        """Test to_str returns correct string for integer and float values."""
        r = Rating(value, scale=scale)
        assert r.to_str() == expected

    def test_to_str_missing_scale(self):
        """Test to_str raises ValueError if scale is missing."""
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)
        r.scale = None
        with pytest.raises(ValueError, match="Rating has no scale"):
            r.to_str(scale=None)

    @pytest.mark.parametrize(
        "value,scale,expected",
        [
            (1.0, RatingScale.ZERO_TO_FIVE, 1),
            (0.5, RatingScale.ZERO_TO_FIVE, 0),  # Python rounds 0.5 to 0
            (0.1, RatingScale.ZERO_TO_FIVE, 0),
            (255, RatingScale.POPM, 255),
            (128, RatingScale.POPM, 128),
        ],
    )
    def test_to_int(self, value, scale, expected):
        """Test to_int returns correct int for various scales."""
        r = Rating(value, scale=scale)
        assert r.to_int() == expected

    def test_to_int_missing_scale(self):
        """Test to_int raises ValueError if scale is missing."""
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)
        r.scale = None
        with pytest.raises(ValueError, match="Rating has no scale"):
            r.to_int(scale=None)

    def test_str_not_implemented(self):
        """Test __str__ raises NotImplementedError."""
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)
        with pytest.raises(NotImplementedError):
            str(r)

    def test_int_not_implemented(self):
        """Test __int__ raises NotImplementedError."""
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)
        with pytest.raises(NotImplementedError):
            int(r)

    def test_float_not_implemented(self):
        """Test __float__ raises NotImplementedError."""
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)
        with pytest.raises(NotImplementedError):
            float(r)

    def test_to_float_missing_scale(self):
        """Test to_float raises ValueError if scale is missing."""
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)
        r.scale = None
        with pytest.raises(ValueError, match="Rating has no scale"):
            r.to_float(scale=None)


class TestRatingDisplay:
    def test_to_display_normal(self):
        """Test to_display returns correct string for normal rating."""
        r = Rating(4.5, scale=RatingScale.ZERO_TO_FIVE)
        # Should display as '4.5' (rounded to 1 decimal)
        assert r.to_display() == "4.5"
        r2 = Rating(5.0, scale=RatingScale.ZERO_TO_FIVE)
        assert r2.to_display() == "5.0"

    def test_to_display_unrated(self):
        """Test to_display returns '0' for unrated rating."""
        r = Rating.unrated()
        assert r.to_display() == "0.0"
        # Also test with explicit scale=None and normalized=0
        r2 = Rating(0, scale=RatingScale.NORMALIZED)
        r2.scale = None
        r2._normalized = 0.0
        assert r2.to_display() == "0"

    @pytest.mark.parametrize(
        "normalized,scale,expected",
        [
            (0.0, RatingScale.NORMALIZED, "0.0"),
            (0.0, RatingScale.ZERO_TO_FIVE, "0.0"),
        ],
    )
    def test_to_display_zero_cases(self, normalized, scale, expected):
        r = Rating(0, scale=scale)
        r._normalized = normalized
        r.scale = scale
        assert r.to_display() == expected


class TestRatingUpdate:
    def test_update_normalized_and_scale(self):
        """Test update sets normalized and updates scale if self.scale is None."""
        r1 = Rating(0.5, scale=None, aggressive=True)
        r2 = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)
        r1.update(r2)
        assert abs(r1._normalized - r2._normalized) < 1e-6
        assert r1.scale == RatingScale.NORMALIZED

    def test_update_does_not_override_scale(self):
        """Test update does not override scale if self.scale is set."""
        r1 = Rating(0.5, scale=RatingScale.ZERO_TO_FIVE)
        r2 = Rating(1.0, scale=RatingScale.ZERO_TO_HUNDRED)
        r1.update(r2)
        assert abs(r1._normalized - r2._normalized) < 1e-6
        assert r1.scale == RatingScale.ZERO_TO_FIVE

    def test_update_both_none_scale(self):
        r1 = Rating(0.5, scale=None, aggressive=True)
        r2 = Rating(1.0, scale=None, aggressive=True)
        r1.update(r2)
        assert abs(r1._normalized - r2._normalized) < 1e-6
        assert r1.scale == RatingScale.NORMALIZED


class TestRatingValidation:
    @pytest.mark.parametrize(
        "value,scale,expected",
        [
            (5.0, RatingScale.ZERO_TO_FIVE, 1.0),
            (2.5, RatingScale.ZERO_TO_FIVE, 0.5),
            (110, RatingScale.ZERO_TO_HUNDRED, None),
            ("bad", RatingScale.ZERO_TO_FIVE, None),
            (0, RatingScale.ZERO_TO_FIVE, 0.0),
            (0.5, RatingScale.ZERO_TO_FIVE, 0.1),
            (255, RatingScale.POPM, 1.0),  # 255 is valid for POPM
            (-1, RatingScale.ZERO_TO_FIVE, None),
            (5.1, RatingScale.ZERO_TO_FIVE, None),
            (100.1, RatingScale.ZERO_TO_HUNDRED, None),
            ([], RatingScale.ZERO_TO_FIVE, None),
            ({}, RatingScale.ZERO_TO_FIVE, None),
            (None, RatingScale.POPM, None),
        ],
    )
    def test_validate(self, value, scale, expected):
        """Test validate returns normalized or None for various values and scales."""
        result = Rating.validate(value, scale)
        assert result == expected

    def test_validate_type_error(self):
        """Test validate returns None for type errors."""
        assert Rating.validate(object(), RatingScale.ZERO_TO_FIVE) is None
        assert Rating.validate(None, RatingScale.ZERO_TO_FIVE) is None


class TestRatingNormalization:
    @pytest.mark.parametrize(
        "value,scale,expected",
        [
            (255, RatingScale.POPM, 1.0),
            (128, RatingScale.POPM, 0.6),
            (0.5, RatingScale.ZERO_TO_FIVE, 0.1),
            (50, RatingScale.ZERO_TO_HUNDRED, 0.5),
            (0, RatingScale.ZERO_TO_FIVE, 0.0),
        ],
    )
    def test_normalize(self, value, scale, expected):
        """Test normalization for all supported scales."""
        r = Rating(value, scale=scale)
        assert abs(r._normalized - expected) < 0.01

    @pytest.mark.parametrize("byte", [-1, 256, 300])
    def test_normalize_popm_out_of_bounds(self, byte):
        """Test _from_popm raises ValueError for out-of-bounds byte."""
        r = Rating(0, scale=RatingScale.POPM)
        with pytest.raises(ValueError, match="Invalid POPM byte value"):
            r._from_popm(byte)


class TestRatingPOPMConversion:
    @pytest.mark.parametrize(
        "normalized,expected_byte",
        [
            (0.1, 13),
            (0.5, 118),
            (1.0, 255),
            (0.0, 0),
            (0.000001, 0),
            (0.049, 0),
            (0.05, 13),
        ],
    )
    def test_to_popm(self, normalized, expected_byte):
        """Test _to_popm returns correct byte for normalized values, including threshold and unrated edge cases."""
        assert Rating._to_popm(normalized) == expected_byte

    @pytest.mark.parametrize(
        "byte,expected_normalized",
        [
            (13, 0.1),
            (118, 0.5),
            (255, 1.0),
            (0, 0.0),
            (1, 0.0),
            (12, 0.1),
            (14, 0.1),
            (31, 0.2),
        ],
    )
    def test_from_popm(self, byte, expected_normalized):
        """Test _from_popm returns correct normalized for valid and edge-case bytes."""
        assert Rating._from_popm(byte) == expected_normalized

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_to_popm_invalid(self, value):
        """Test _to_popm raises ValueError for invalid values."""
        r = Rating(0, scale=RatingScale.POPM)
        with pytest.raises(ValueError):
            r._to_popm(value)

    @pytest.mark.parametrize("byte", [-1, 256])
    def test_from_popm_invalid(self, byte):
        """Test _from_popm raises ValueError for invalid byte."""
        r = Rating(0, scale=RatingScale.POPM)
        with pytest.raises(ValueError):
            r._from_popm(byte)

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_to_popm_out_of_range(self, value):
        """Test _to_popm returns None or 255 for normalized values above 1."""
        with pytest.raises(ValueError):
            Rating._to_popm(value)


class TestRatingInference:
    @pytest.mark.parametrize(
        "value,expected_non_aggressive,expected_aggressive",
        [
            (255, RatingScale.POPM, RatingScale.POPM),  # in POPM_MAP
            (101, RatingScale.POPM, RatingScale.POPM),  # 101-255, only POPM makes sense
            (11, None, RatingScale.ZERO_TO_HUNDRED),  # ambiguous: aggressive favors 0-100
            (0.8, RatingScale.NORMALIZED, RatingScale.NORMALIZED),  # unambiguous normalized
            (2.5, RatingScale.ZERO_TO_FIVE, RatingScale.ZERO_TO_FIVE),  # unambiguous 0-5
            (1.0, None, RatingScale.NORMALIZED),  # ambiguous: aggressive favors normalized
            (3.0, RatingScale.ZERO_TO_FIVE, RatingScale.ZERO_TO_FIVE),  # unambiguous 0-5
            (5.0, None, RatingScale.ZERO_TO_FIVE),  # ambiguous: aggressive favors 0-5
            (0.0, None, None),  # unrated
            (100, RatingScale.ZERO_TO_HUNDRED, RatingScale.ZERO_TO_HUNDRED),  # unambiguous 0-100
            (90, RatingScale.ZERO_TO_HUNDRED, RatingScale.ZERO_TO_HUNDRED),  # unambiguous 0-100
            (7, RatingScale.ZERO_TO_TEN, RatingScale.ZERO_TO_TEN),  # unambiguous 0-10
            (10, None, RatingScale.ZERO_TO_TEN),  # ambiguous: aggressive favors 0-10
            (-1, None, None),  # negative, invalid
            (256, None, None),  # out of range, invalid
            (float("nan"), None, None),  # nan, invalid
            (float("inf"), None, None),  # inf, invalid
            (float("-inf"), None, None),  # -inf, invalid
            (True, None, None),  # bool: invalid
        ],
    )
    def test_infer_various_values_expected_scales(self, value, expected_non_aggressive, expected_aggressive):
        result_non_aggressive = Rating.infer(value, aggressive=False)
        result_aggressive = Rating.infer(value, aggressive=True)
        assert result_non_aggressive == expected_non_aggressive
        assert result_aggressive == expected_aggressive

    def test_infer_returns_none_on_unexpected_exception(self):
        class RaisesRuntimeError:
            def __float__(self):
                raise RuntimeError("custom error")

        assert Rating.infer(RaisesRuntimeError()) is None


class TestRatingTryCreate:
    @pytest.mark.parametrize(
        "value,scale,expected_none",
        [
            (None, None, True),
            ("", None, True),
            ("bad", None, True),
            (5.0, RatingScale.ZERO_TO_FIVE, False),
            (255, RatingScale.POPM, False),
            (110, RatingScale.ZERO_TO_HUNDRED, True),  # out of bounds
            (0.5, None, False),  # aggressive default
            (0, RatingScale.POPM, False),
        ],
    )
    def test_try_create(self, value, scale, expected_none):
        """Test try_create returns Rating or None for various inputs."""
        result = Rating.try_create(value, scale)
        if expected_none:
            assert result is None
        else:
            assert isinstance(result, Rating)


class TestRatingUnrated:
    def test_unrated_behavior(self):
        """Test unrated returns a Rating with is_unrated True and to_float 0."""
        r = Rating.unrated()
        assert r.is_unrated
        assert r.to_float() == 0.0
        assert r.scale == RatingScale.NORMALIZED

    def test_unrated_attributes(self):
        r = Rating.unrated()
        assert r.is_unrated
        assert r.scale == RatingScale.NORMALIZED
        assert r._normalized == 0.0
        assert r.raw == 0


class TestRatingHash:
    def test_hash(self):
        """Test __hash__ returns hash of normalized value."""
        r1 = Rating(2.5, scale=RatingScale.ZERO_TO_FIVE)
        r2 = Rating(0.5, scale=RatingScale.ZERO_TO_FIVE)
        assert hash(r1) == hash(r1._normalized)
        assert hash(r2) == hash(r2._normalized)
        # Hashes for different normalized values should differ
        assert hash(r1) != hash(r2)


class TestRatingComparison:
    @pytest.mark.parametrize(
        "val1,val2,eq,lt,gt,le,ge",
        [
            (Rating(0.5, RatingScale.ZERO_TO_FIVE), Rating(13, RatingScale.POPM), True, False, False, True, True),
            (Rating(0.5, RatingScale.ZERO_TO_FIVE), Rating(64, RatingScale.POPM), False, True, False, True, False),
            (Rating(255, RatingScale.POPM), Rating(4.5, RatingScale.ZERO_TO_FIVE), False, False, True, False, True),
            (Rating(0.5, RatingScale.ZERO_TO_FIVE), 0.1, True, False, False, True, True),
            (Rating(0.5, RatingScale.ZERO_TO_FIVE), 0.5, False, True, False, True, False),
            (Rating(0.0, RatingScale.ZERO_TO_FIVE), 0.0, True, False, False, True, True),
        ],
    )
    def test_comparison_operators(self, val1, val2, eq, lt, gt, le, ge):
        """Test ==, <, >, <=, >= with Rating and float/int."""
        assert (val1 == val2) == eq
        assert (val1 < val2) == lt
        assert (val1 > val2) == gt
        assert (val1 <= val2) == le
        assert (val1 >= val2) == ge

    def test_comparison_not_implemented(self):
        """Test comparison returns NotImplemented for unsupported types."""
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)

        class Dummy:
            pass

        d = Dummy()
        # __eq__ returns NotImplemented for unsupported types
        assert r.__eq__(d) is NotImplemented
        assert r.__lt__(d) is NotImplemented
        assert r.__le__(d) is NotImplemented
        assert r.__gt__(d) is NotImplemented
        assert r.__ge__(d) is NotImplemented

    def test_comparison_with_none(self):
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)
        assert r.__eq__(None) is NotImplemented
        assert r.__lt__(None) is NotImplemented
        assert r.__le__(None) is NotImplemented
        assert r.__gt__(None) is NotImplemented
        assert r.__ge__(None) is NotImplemented

    def test_comparison_with_unrelated_type(self):
        r = Rating(1.0, scale=RatingScale.ZERO_TO_FIVE)

        class Unrelated:
            pass

        u = Unrelated()
        assert r.__eq__(u) is NotImplemented
        assert r.__lt__(u) is NotImplemented
        assert r.__le__(u) is NotImplemented
        assert r.__gt__(u) is NotImplemented
        assert r.__ge__(u) is NotImplemented

    def test_comparison_with_unrated(self):
        """Test comparison where one rating is unrated."""
        r1 = Rating.unrated()
        r2 = Rating(0.5, scale=RatingScale.ZERO_TO_FIVE)
        assert (r1 < r2) is True
        assert (r1 == r2) is False
        assert (r2 > r1) is True


class TestRatingIsLikely:
    def test_is_likely_returns_true_for_normalized_value(self):
        assert Rating._is_likely(0.5, RatingScale.NORMALIZED) is True
        assert Rating._is_likely(1.5, RatingScale.NORMALIZED) is False
