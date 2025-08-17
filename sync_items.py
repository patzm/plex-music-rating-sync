from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from MediaPlayer import MediaPlayer


class AudioTag(object):
    MAX_ARTIST_LENGTH = 25
    MAX_ALBUM_LENGTH = 30
    MAX_TITLE_LENGTH = 40
    MAX_FILE_PATH_LENGTH = 50
    DISPLAY_HEADER = f"{'':<7} {'Track':<5} {'Artist':<{MAX_ARTIST_LENGTH}} {'Album':<{MAX_ALBUM_LENGTH}} {'Title':<{MAX_TITLE_LENGTH}} {'File Path':<{MAX_FILE_PATH_LENGTH}}"

    def __init__(self, artist: str = "", album: str = "", title: str = "", **kwargs):
        self.ID = kwargs.get("ID", None)
        self.album = album
        self.artist = artist
        self.title = title
        self.rating = kwargs.get("rating")
        self.genre = kwargs.get("genre")
        self.file_path = kwargs.get("file_path")
        self.track = int(kwargs.get("track")) if kwargs.get("track") else None
        self.duration = kwargs.get("duration", -1)

    def __str__(self) -> str:
        # Safely handle None values for artist, album, title
        return " - ".join([str(x) if x is not None else "" for x in [self.artist, self.album, self.title]])

    def __repr__(self) -> str:
        return f"AudioTag({self!s})"

    @staticmethod
    def truncate(value: str, length: int, from_end: bool = True, default: str = "N/A") -> str:
        """Truncate a string to the specified length, adding '...' at the start or end."""
        value = str(value or default)
        if len(value) <= length:
            return value
        return f"{value[: length - 3]}..." if from_end else f"...{value[-(length - 3) :]}"

    def details(self, player: Optional["MediaPlayer"] = None) -> str:
        """Print formatted track details."""
        track_number = self.track or 0
        track_rating = self.rating.to_display() if self.rating else "N/A"
        artist = self.truncate(self.artist, self.MAX_ARTIST_LENGTH)
        album = self.truncate(self.album, self.MAX_ALBUM_LENGTH)
        title = self.truncate(self.title, self.MAX_TITLE_LENGTH)
        file_path = self.truncate(self.file_path, self.MAX_FILE_PATH_LENGTH, from_end=False)
        player_rating = f"{player.abbr if player else '  '}[{track_rating}]"
        return (
            f"{player_rating:<7} {track_number:{5}} {artist:<{self.MAX_ARTIST_LENGTH}} "
            f"{album:<{self.MAX_ALBUM_LENGTH}} {title:<{self.MAX_TITLE_LENGTH}} "
            f"{file_path:<{self.MAX_FILE_PATH_LENGTH}}"
        )

    @staticmethod
    def get_fields() -> List[str]:
        """Get list of fields that should be cached"""
        return ["ID", "album", "artist", "title", "rating", "genre", "file_path", "track", "duration"]

    def to_dict(self) -> dict:
        """Convert to dictionary for caching"""
        return {field: getattr(self, field) for field in self.get_fields()}

    @classmethod
    def from_dict(cls, data: dict) -> "AudioTag":
        """Create AudioTag from cached dictionary"""
        return cls(**data)


class Playlist(object):
    def __init__(self, ID: str | int, name: str):
        self.ID = ID
        self.name = name
        self.tracks = []
        self.is_auto_playlist = False
        self.is_extm3u = True

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        return f"Playlist: {self.name}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            return NotImplemented
        return self.name.lower() == other.name.lower()

    def has_track(self, track: AudioTag) -> bool:
        exists = any(t.title.lower() == track.title.lower() and t.artist.lower() == track.artist.lower() for t in self.tracks)
        return exists

    def missing_tracks(self, other: "Playlist") -> List[AudioTag]:
        if not isinstance(other, type(self)):
            return []
        missing = [t for t in other.tracks if not self.has_track(t)]
        return missing

    @property
    def num_tracks(self) -> int:
        return len(self.tracks)
