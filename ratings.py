from enum import Enum
from typing import Optional, Union


class RatingScale(Enum):
    NORMALIZED = 1.0
    ZERO_TO_FIVE = 5.0
    ZERO_TO_TEN = 10.0
    ZERO_TO_HUNDRED = 100.0
    POPM = 255


POPM_MAP = [
    (0.0, 0),
    (0.1, 13),
    (0.2, 32),
    (0.3, 54),
    (0.4, 64),
    (0.5, 118),
    (0.6, 128),
    (0.7, 186),
    (0.8, 196),
    (0.9, 242),
    (1.0, 255),
]


class Rating:
    def __init__(self, raw: float | str, scale: RatingScale | None = None, *, aggressive: bool = False) -> None:
        self.raw = raw
        value = float(self.raw)

        self.scale = scale or self.infer(value, aggressive=aggressive)
        if not self.scale:
            raise ValueError(f"Unable to infer rating scale from value: {raw}")

        self._normalized = self._normalize(value, self.scale)

        if not (0.0 <= self._normalized <= 1.0):
            raise ValueError(f"Normalized rating out of bounds: {self._normalized} for value {value} with scale {self.scale.name}")

    def __str__(self) -> str:
        raise NotImplementedError("Use `to_str()` or `to_display()` for explicit rating string output.")

    def __repr__(self) -> str:
        return f"<Rating raw={self.raw}, normalized={self._normalized:.3f}, scale={self.scale.name}>"

    @property
    def is_unrated(self) -> bool:
        return self._normalized <= 0.0

    @staticmethod
    def infer(value: float, aggressive: bool = False) -> RatingScale | None:
        try:
            if value is None or isinstance(value, bool):
                return None
            v = float(value)
            if v != v or v in (float("inf"), float("-inf")):
                return None
            elif v <= 0 or v > 255:
                return None
            elif Rating._is_popm_byte(v):
                return RatingScale.POPM
            elif 101 <= v <= 255:
                return RatingScale.POPM
            else:
                return Rating._infer_0_100(v, aggressive)
        except Exception:
            return None

    @staticmethod
    def _is_popm_byte(v: float) -> bool:
        popm_bytes = {byte for _, byte in POPM_MAP[1:]}
        return v in popm_bytes

    @staticmethod
    def _is_likely(value: float, scale: RatingScale) -> bool:
        # Ambiguous values: 1.0, 5.0, 10.0, 0.0 (except unrated)
        if value in (0.0, 1.0, 5.0, 10.0, 0.0):
            return False
        # For 0-5: 1 <= v <= 5, step 0.5
        elif scale == RatingScale.ZERO_TO_FIVE:
            return 1 <= value <= 5 and (value * 2).is_integer()
        # For 0-10: 1 <= v <= 10, integer
        elif scale == RatingScale.ZERO_TO_TEN:
            return 1 <= value <= 10 and float(value).is_integer()
        # For 0-100: 12 <= value <= 100, integer (treat 11 as ambiguous)
        elif scale == RatingScale.ZERO_TO_HUNDRED:
            return 12 <= value <= 100 and float(value).is_integer()
        # For NORMALIZED: 0 < v < 1
        else:
            return 0 < value < 1

    @staticmethod
    def _infer_0_100(v: float, aggressive: bool) -> RatingScale | None:
        if not aggressive:
            return Rating._infer_0_100_non_aggressive(v)
        return Rating._infer_0_100_aggressive(v)

    @staticmethod
    def _infer_0_100_non_aggressive(v: float) -> RatingScale | None:
        # 0 < v < 1: only normalized
        if 0 < v < 1:
            return RatingScale.NORMALIZED
        # 1 <= v <= 5, step 0.5: only 0-5, but ambiguous if v in (1.0, 5.0)
        if 1 <= v <= 5 and (v * 2).is_integer():
            if Rating._is_likely(v, RatingScale.ZERO_TO_FIVE):
                return RatingScale.ZERO_TO_FIVE
        # 1 <= v <= 10, integer: only 0-10, but ambiguous if v in (1.0, 10.0)
        if 1 <= v <= 10 and float(v).is_integer():
            if Rating._is_likely(v, RatingScale.ZERO_TO_TEN):
                return RatingScale.ZERO_TO_TEN
        # 12 <= v <= 100, integer: only 0-100 (treat 11 as ambiguous)
        if 11 <= v <= 100 and float(v).is_integer():
            if Rating._is_likely(v, RatingScale.ZERO_TO_HUNDRED):
                return RatingScale.ZERO_TO_HUNDRED
        return None

    @staticmethod
    def _infer_0_100_aggressive(v: float) -> RatingScale | None:
        # Special case: for 1.0, always favor NORMALIZED
        if v == 1.0:
            return RatingScale.NORMALIZED
        candidates = [
            (RatingScale.NORMALIZED, 1.0, lambda x: 0 < x < 1),
            (RatingScale.ZERO_TO_FIVE, 5.0, lambda x: 1 <= x <= 5 and (x * 2).is_integer()),
            (RatingScale.ZERO_TO_TEN, 10.0, lambda x: 1 <= x <= 10 and float(x).is_integer()),
            (RatingScale.ZERO_TO_HUNDRED, 100.0, lambda x: 12 <= x <= 100 and float(x).is_integer()),
        ]
        for scale, _, pred in candidates:
            if pred(v):
                return scale
        # If no match, pick the scale with the max value closest to v, but not over
        for scale, maxval, _ in candidates:
            if v <= maxval:
                return scale
        return None  # pragma: no cover

    @classmethod
    def unrated(cls) -> "Rating":
        return cls(0, scale=RatingScale.NORMALIZED)

    @classmethod
    def try_create(cls, value: str | float, scale: RatingScale | None = None, *, aggressive: bool = False) -> Optional["Rating"]:
        if value is None or value == "":
            return None
        try:
            return cls(value, scale=scale, aggressive=aggressive)
        except ValueError:
            return None

    def to_float(self, scale: RatingScale | None = None) -> float:
        """Return this rating as a float in the given scale (default: self.scale)."""
        target_scale = scale or self.scale
        if target_scale is None:
            raise ValueError("Rating has no scale. Provide one to convert to float.")
        if target_scale == RatingScale.POPM:
            return self._to_popm(self._normalized)
        return round(self._normalized * target_scale.value, 3)

    def to_str(self, scale: RatingScale | None = None) -> str:
        """Convert the rating to a string for writing to file (e.g., POPM, Vorbis)."""
        target_scale = scale or self.scale
        if target_scale is None:
            raise ValueError("Rating has no scale. Provide one to convert to string.")

        value = self.to_float(target_scale)
        if value.is_integer():
            return str(int(value))
        return str(round(value, 1))

    def to_int(self, scale: RatingScale | None = None) -> int:
        """Return the rating as a rounded int in the target scale (e.g., for POPM)."""
        target_scale = scale or self.scale
        if target_scale is None:
            raise ValueError("Rating has no scale. Provide one to convert to integer.")
        return round(self.to_float(target_scale))

    def to_display(self, scale: RatingScale = RatingScale.ZERO_TO_FIVE) -> str:
        """Return a user-friendly rating string in a 5-star style, e.g., '4.5 / 5.0'."""
        if self.scale is None and self._normalized == 0.0:
            return "0"  # Sentinel or unrated — no need for scale context
        return f"{round(self.to_float(scale), 1)}"

    def update(self, other: "Rating") -> None:
        """Update this rating with another's normalized value."""
        self._normalized = other._normalized
        self.scale = other.scale if self.scale is None else self.scale

    @staticmethod
    def validate(value: str | float, scale: RatingScale = RatingScale.ZERO_TO_FIVE) -> float | None:
        """
        Validate that a value is within the expected scale and aligned to 0.5 steps (if applicable).
        Returns the normalized value or None if invalid.
        """
        try:
            val = float(value)
            if 0 <= val <= scale.value and (val * 2).is_integer():
                return round(val / scale.value, 3)
        except (ValueError, TypeError):
            pass
        return None

    @classmethod
    def _normalize(cls, value: float, scale: RatingScale) -> float:
        if scale == RatingScale.POPM:
            return cls._from_popm(value)
        return round(value / scale.value, 3)

    @staticmethod
    def _from_popm(byte: float) -> float:
        if not (0 <= byte <= 255):
            raise ValueError(f"Invalid POPM byte value: {byte}. Must be between 0 and 255.")
        best_match = min(POPM_MAP, key=lambda pair: abs(pair[1] - byte))
        return best_match[0]

    @staticmethod
    def _to_popm(normalized: float) -> int:
        if not (0.0 <= normalized <= 1.0):
            raise ValueError(f"Invalid POPM byte value: {normalized}. Must be between 0 and 255.")
        if normalized < 0.05:
            return 0
        for rating, byte in POPM_MAP:
            if normalized <= rating:
                return byte
        return None  # pragma: no cover

    def __float__(self):
        raise NotImplementedError("Use `to_float()` with an optional scale parameter.")

    def __int__(self):
        raise NotImplementedError("Use `to_int()` with an optional scale parameter.")

    def __hash__(self):
        return hash(self._normalized)

    def __eq__(self, other: Union["Rating", float]) -> bool:
        if isinstance(other, Rating):
            return self._normalized == other._normalized
        elif isinstance(other, (int, float)):
            return self._normalized == other
        return NotImplemented

    def __lt__(self, other: Union["Rating", float]) -> bool:
        if isinstance(other, Rating):
            return self._normalized < other._normalized
        elif isinstance(other, (int, float)):
            return self._normalized < other
        return NotImplemented

    def __le__(self, other: Union["Rating", float]) -> bool:
        if isinstance(other, Rating):
            return self._normalized <= other._normalized
        elif isinstance(other, (int, float)):
            return self._normalized <= other
        return NotImplemented

    def __gt__(self, other: Union["Rating", float]) -> bool:
        if isinstance(other, Rating):
            return self._normalized > other._normalized
        elif isinstance(other, (int, float)):
            return self._normalized > other
        return NotImplemented

    def __ge__(self, other: Union["Rating", float]) -> bool:
        if isinstance(other, Rating):
            return self._normalized >= other._normalized
        elif isinstance(other, (int, float)):
            return self._normalized >= other
        return NotImplemented
