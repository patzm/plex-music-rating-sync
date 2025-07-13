import io
from typing import List

from mutagen.id3 import ID3, POPM, TALB, TIT2, TPE1, TRCK, TXXX

from filesystem_provider import ID3Field
from ratings import Rating, RatingScale


def get_popm_email(tag_name: str) -> str:
    return tag_name.split(":", 1)[1] if ":" in tag_name else ""


def make_raw_rating(tag_key: str, normalized: float | str, *, rating_scale: RatingScale | None = None) -> str | int:
    """
    Returns a raw rating value for a given tag key.

    - If rating_scale is omitted, it is inferred from tag_key.
    - Output type is inferred from tag_key:
        - POPM-like tags → int
        - All others → str
    """
    is_string = False
    if isinstance(normalized, str):
        is_string = True
        normalized = float(normalized)

    rating = Rating(normalized, scale=RatingScale.NORMALIZED)

    key = tag_key.upper()

    # Infer scale if not explicitly provided
    scale = rating_scale
    if scale is None:
        if key == "TEXT" or key == "TXXX:RATING":
            scale = RatingScale.ZERO_TO_FIVE
        elif key == "FMPS_RATING":
            scale = RatingScale.NORMALIZED
        elif key == "RATING":
            raise ValueError("Ambiguous scale for 'RATING'; must explicitly pass rating_scale.")
        elif key.startswith("POPM:"):
            scale = RatingScale.POPM
        else:
            raise ValueError(f"Unknown tag_key '{tag_key}'; must specify rating_scale.")

    # Determine return format
    if scale == RatingScale.POPM:
        value = rating.to_int(RatingScale.POPM)
    value = rating.to_str(scale)
    return str(value) if is_string else value


def _update_basic_metadata(audio_file, *, album=None, artist=None, title=None, track=None):
    """Update basic ID3 metadata tags if provided."""
    if album is not None:
        audio_file.tags[ID3Field.ALBUM] = TALB(encoding=3, text=album)
    if artist is not None:
        audio_file.tags[ID3Field.ARTIST] = TPE1(encoding=3, text=artist)
    if title is not None:
        audio_file.tags[ID3Field.TITLE] = TIT2(encoding=3, text=title)
    if track is not None:
        audio_file.tags[ID3Field.TRACKNUMBER] = TRCK(encoding=3, text=str(track))


def _update_txxx_rating(audio_file, rating_obj):
    """Update or add TXXX:RATING tag."""
    txt = rating_obj.to_str(RatingScale.ZERO_TO_FIVE)
    if "TXXX:RATING" in audio_file.tags:
        audio_file.tags["TXXX:RATING"].text = [txt]
    else:
        audio_file.tags.add(TXXX(encoding=1, desc="RATING", text=[txt]))


def _update_popm_rating(audio_file, rating_obj, tag_name):
    """Update or add POPM rating tag."""
    value = rating_obj.to_int(RatingScale.POPM)
    email = tag_name.split(":", 1)[1] if ":" in tag_name else ""
    if tag_name in audio_file.tags:
        audio_file.tags[tag_name].rating = value
    else:
        audio_file.tags.add(POPM(email=email, rating=value, count=0))


def add_or_update_id3frame(
    audio_file,
    *,
    album: str | None = None,
    artist: str | None = None,
    title: str | None = None,
    track: str | None = None,
    rating: Rating | float | None = None,
    rating_tags: List[str] | str | None = None,
):
    """
    Updates ID3 tags in an audio file, including basic metadata and ratings.

    Returns:
        Updated audio file object
    """
    # Create ID3 tags if they don't exist
    if audio_file.tags is None:
        audio_file.tags = ID3()

    # Update basic metadata
    _update_basic_metadata(audio_file, album=album, artist=artist, title=title, track=track)

    # Update ratings if provided
    if rating is not None:
        if not isinstance(rating, Rating):
            rating = Rating(rating, scale=RatingScale.NORMALIZED)

        if not rating_tags:
            raise ValueError("Rating tags must be provided if rating is specified.")

        # Create Rating object with normalized value
        tags_list = [rating_tags] if isinstance(rating_tags, str) else rating_tags

        for tag in tags_list:
            if tag == "TXXX:RATING":
                _update_txxx_rating(audio_file, rating)
            elif tag.upper().startswith("POPM:"):
                _update_popm_rating(audio_file, rating, tag)

    return audio_file


class WriteSpy(io.StringIO):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.write_calls = []
        self.writelines_calls = []

    def write(self, s):
        self.write_calls.append(s)
        return super().write(s)

    def writelines(self, lines):
        self.writelines_calls.append(list(lines))
        return super().writelines(lines)

    def open_for_mode(self, mode):
        if "w" in mode:
            self.seek(0)
            self.truncate(0)
        elif "a" in mode:
            self.seek(0, io.SEEK_END)
        else:
            self.seek(0)
        return self


def generate_playlist_content(lines, is_extm3u=False, title=None):
    """Generate playlist file content as a string."""
    header = []
    if is_extm3u:
        header.append("#EXTM3U")
        if title:
            header.append(f"#PLAYLIST:{title}")
    content_lines = header
    for entry in lines:
        if isinstance(entry, dict):
            extinf = entry.get("extinf")
            path = entry.get("path")
            if is_extm3u and extinf:
                content_lines.append(f"#EXTINF:{extinf}")
            if path:
                content_lines.append(path)
        else:
            content_lines.append(entry)
    content = "\n".join(content_lines)
    if content and not content.endswith("\n"):
        content += "\n"
    return content
