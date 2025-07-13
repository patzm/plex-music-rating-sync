import pytest

from sync_items import AudioTag, Playlist


class TestAudioTag:
    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            ({}, {"ID": None, "album": "", "artist": "", "title": "", "rating": None, "genre": None, "file_path": None, "track": None, "duration": -1}),
            (
                {"ID": "id", "album": "a", "artist": "b", "title": "c", "rating": 5, "genre": "g", "file_path": "f", "track": "3", "duration": 10},
                {"ID": "id", "album": "a", "artist": "b", "title": "c", "rating": 5, "genre": "g", "file_path": "f", "track": 3, "duration": 10},
            ),
            ({"track": 7}, {"track": 7}),
            ({"track": None}, {"track": None}),
            ({"duration": 42}, {"duration": 42}),
        ],
    )
    def test_audio_tag_init_variants(self, kwargs, expected):
        """AudioTag initializes with various field combinations."""
        tag = AudioTag(**kwargs)
        for k, v in expected.items():
            if k in kwargs or k in ["track", "duration"]:
                assert getattr(tag, k) == v

    @pytest.mark.parametrize(
        "artist,album,title,expected_str",
        [
            ("", "", "", " -  - "),
            ("A", "B", "C", "A - B - C"),
        ],
    )
    def test_audio_tag_str_and_repr(self, artist, album, title, expected_str):
        """__str__ and __repr__ return correct values."""
        tag = AudioTag(artist=artist, album=album, title=title)
        assert str(tag) == expected_str
        assert expected_str in repr(tag)

    @pytest.mark.parametrize(
        "value,length,from_end,default,expected",
        [
            ("short", 10, True, "N/A", "short"),
            ("exactlyten", 10, True, "N/A", "exactlyten"),
            ("toolongvalue", 7, True, "N/A", "tool..."),
            ("toolongvalue", 7, False, "N/A", "...alue"),
            (None, 5, True, "DEF", "DEF"),
            ("", 5, True, "DEF", "DEF"),
        ],
    )
    def test_audio_tag_truncate(self, value, length, from_end, default, expected):
        """truncate handles all edge cases and truncation logic."""
        assert AudioTag.truncate(value, length, from_end, default) == expected

    @pytest.mark.parametrize(
        "fields,player,expected_substrings",
        [
            ({}, None, ["N/A", "0"]),
            ({"artist": "A", "album": "B", "title": "C", "file_path": "F", "track": 2, "rating": None}, None, ["A", "B", "C", "F", "0" if None else "2"]),
            (
                {"artist": "A" * 30, "album": "B" * 40, "title": "C" * 50, "file_path": "F" * 60},
                None,
                [
                    "...",
                ],
            ),
        ],
    )
    def test_audio_tag_details(self, fields, player, expected_substrings):
        """details returns expected string for various field combinations."""
        tag = AudioTag(**fields)
        result = tag.details(player)
        for substr in expected_substrings:
            assert substr in result

    @pytest.mark.parametrize("file_path,expected", [(None, "N/A"), ("", "N/A"), ("F" * 60, "...")])
    def test_audio_tag_details_file_path_variants(self, file_path, expected):
        """details handles file_path edge cases and truncation."""
        tag = AudioTag(file_path=file_path)
        result = tag.details()
        assert expected in result

    def test_audio_tag_details_with_and_without_player(self):
        """details includes or omits player.abbr as appropriate."""

        class DummyPlayer:
            abbr = "DP"

        tag = AudioTag()
        assert "DP" in tag.details(DummyPlayer())
        assert "  " in tag.details(None)

    def test_audio_tag_get_fields(self):
        """get_fields returns the expected static list."""
        assert AudioTag.get_fields() == ["ID", "album", "artist", "title", "rating", "genre", "file_path", "track", "duration"]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {},
            {"ID": "id", "album": "a", "artist": "b", "title": "c", "rating": 5, "genre": "g", "file_path": "f", "track": 3, "duration": 10},
        ],
    )
    def test_audio_tag_to_dict(self, kwargs):
        """to_dict returns dict with correct values."""
        tag = AudioTag(**kwargs)
        d = tag.to_dict()
        for k in AudioTag.get_fields():
            assert k in d
            assert d[k] == getattr(tag, k)

    @pytest.mark.parametrize(
        "data",
        [
            {"ID": "id", "album": "a", "artist": "b", "title": "c", "rating": 5, "genre": "g", "file_path": "f", "track": 3, "duration": 10},
            {"ID": "id"},
        ],
    )
    def test_audio_tag_from_dict(self, data):
        """from_dict creates AudioTag with correct values, using defaults as needed."""
        tag = AudioTag.from_dict(data)
        for k in data:
            assert getattr(tag, k) == data[k]


class TestPlaylist:
    @pytest.mark.parametrize(
        "ID,name,expected_str",
        [
            ("abc", "MyList", "Playlist: MyList"),
            (123, "MyList", "Playlist: MyList"),
            ("abc", "", "Playlist: "),
        ],
    )
    def test_init_and_str_repr(self, ID, name, expected_str):
        """Covers Playlist construction with string/int ID and empty/non-empty name. Also checks __str__ and __repr__."""
        pl = Playlist(ID, name)
        assert pl.ID == ID
        assert pl.name == name
        assert str(pl) == expected_str
        assert repr(pl) == expected_str

    @pytest.mark.parametrize(
        "self_name,other,expected",
        [
            ("A", None, False),
            ("A", 123, False),
            ("A", Playlist("id", "A"), True),
            ("A", Playlist("id", "a"), True),
            ("A", Playlist("id", "B"), False),
            ("", Playlist("id", ""), True),
            ("", Playlist("id", "notempty"), False),
        ],
    )
    def test_eq_various(self, self_name, other, expected):
        """Covers Playlist equality: non-Playlist (should be False), case-insensitive, empty names, and different names."""
        pl = Playlist("id", self_name)
        assert (pl == other) is expected

    @pytest.mark.parametrize(
        "self_name,other,expected",
        [
            ("A", None, NotImplemented),
            ("A", 123, NotImplemented),
        ],
    )
    def test_eq_dunder_notimplemented(self, self_name, other, expected):
        """Direct __eq__ call returns NotImplemented for non-Playlist types."""
        pl = Playlist("id", self_name)
        assert pl.__eq__(other) is expected

    @pytest.mark.parametrize(
        "playlist_tracks,query_track,expected",
        [
            ([], AudioTag(title="Song", artist="Artist"), False),
            ([AudioTag(title="Other", artist="Other")], AudioTag(title="Song", artist="Artist"), False),
            ([AudioTag(title="Song", artist="Artist")], AudioTag(title="song", artist="artist"), True),
            ([AudioTag(title="A", artist="B"), AudioTag(title="Song", artist="Artist")], AudioTag(title="song", artist="artist"), True),
            ([AudioTag(title="", artist="Artist")], AudioTag(title="", artist="artist"), True),
            ([AudioTag(title="Song", artist="")], AudioTag(title="song", artist=""), True),
        ],
    )
    def test_has_track_various(self, playlist_tracks, query_track, expected):
        """Covers has_track with empty tracks, no match, case-insensitive match, multiple tracks, and empty title/artist."""
        pl = Playlist("id", "pl")
        pl.tracks = playlist_tracks
        assert pl.has_track(query_track) is expected

    @pytest.mark.parametrize(
        "self_tracks,other,other_tracks,expected_missing",
        [
            # other is not a Playlist
            ([AudioTag(title="A", artist="B")], None, None, []),
            ([AudioTag(title="A", artist="B")], 123, None, []),
            # other is Playlist, other's tracks empty
            ([AudioTag(title="A", artist="B")], Playlist("id2", "pl2"), [], []),
            ([], Playlist("id2", "pl2"), [], []),
            # all other's tracks present in self
            ([AudioTag(title="A", artist="B")], Playlist("id2", "pl2"), [AudioTag(title="A", artist="B")], []),
            # some missing
            ([AudioTag(title="A", artist="B")], Playlist("id2", "pl2"), [AudioTag(title="A", artist="B"), AudioTag(title="C", artist="D")], [AudioTag(title="C", artist="D")]),
            # all missing
            ([AudioTag(title="A", artist="B")], Playlist("id2", "pl2"), [AudioTag(title="C", artist="D")], [AudioTag(title="C", artist="D")]),
            # self.tracks empty, other's tracks non-empty
            ([], Playlist("id2", "pl2"), [AudioTag(title="A", artist="B")], [AudioTag(title="A", artist="B")]),
        ],
    )
    def test_missing_tracks_various(self, self_tracks, other, other_tracks, expected_missing):
        """Covers missing_tracks with non-Playlist, empty other's tracks, all present, some missing, all missing, and self.tracks empty."""
        pl = Playlist("id", "pl")
        pl.tracks = self_tracks
        if not isinstance(other, Playlist):
            assert pl.missing_tracks(other) == []
        else:
            other.tracks = other_tracks
            result = pl.missing_tracks(other)

            # Compare by title/artist for equality
            def tags_eq(a, b):
                return a.title == b.title and a.artist == b.artist

            assert len(result) == len(expected_missing)
            for i in range(len(result)):
                assert tags_eq(result[i], expected_missing[i])

    @pytest.mark.parametrize(
        "tracks,expected_count",
        [
            ([], 0),
            ([AudioTag(title="A", artist="B")], 1),
            ([AudioTag(title="A", artist="B"), AudioTag(title="C", artist="D")], 2),
        ],
    )
    def test_num_tracks(self, tracks, expected_count):
        """Covers num_tracks property for empty and non-empty track lists."""
        pl = Playlist("id", "pl")
        pl.tracks = tracks
        assert pl.num_tracks == expected_count
