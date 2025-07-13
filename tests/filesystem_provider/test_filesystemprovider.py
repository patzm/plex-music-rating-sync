from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mutagen.id3 import ID3FileType

from filesystem_provider import FileSystemProvider
from manager import get_manager
from ratings import Rating
from sync_items import AudioTag, Playlist
from tests.helpers import WriteSpy, generate_playlist_content


@pytest.fixture
def fsp_instance(request, tmp_path, monkeypatch):
    """FileSystemProvider with real ConfigManager, patched for test isolation."""
    cfg = getattr(request, "param", {})
    audio_root = tmp_path / "music"
    playlist_root = tmp_path / "playlists"

    config = get_manager().get_config_manager()
    config.path = audio_root
    config.playlist_path = playlist_root
    config.dry = cfg.get("dry", False)

    # from
    # config_mgr = ConfigManager()

    # get_manager = MagicMock(return_value=config_mgr)

    # monkeypatch.setattr("manager.get_manager", lambda: get_manager)

    return FileSystemProvider()


@pytest.fixture
def file_factory():
    """Create fake file objects with suffix and name."""

    def _factory(suffix, filename):
        file = type("FakeFile", (), {})()
        file.suffix = suffix
        file.filename = filename
        return file

    return _factory


@pytest.fixture
def playlist_factory():
    """Create in-memory playlist files for tests."""

    @contextmanager
    def _factory(playlist_root, monkeypatch, filename, lines, is_extm3u=False, title=None, exists=True, open_raises=False, track_exists_map=None):
        if playlist_root is None or filename is None:
            raise ValueError("playlist_root and filename are required for playlist_factory")
        if lines is None:
            raise ValueError("lines must be provided for playlist_factory")

        playlist_root = Path(playlist_root)
        playlist_root.mkdir(parents=True, exist_ok=True)
        playlist_path = playlist_root / filename
        playlist_content = WriteSpy()
        playlist_content.close = lambda: None
        if track_exists_map is None:
            track_exists_map = {}

        def open_patch(path_self, mode="r", encoding=None, *args, **kwargs):
            if path_self == playlist_path:
                if open_raises:
                    raise OSError("Simulated open error")
                return playlist_content.open_for_mode(mode)
            return Path.__original_open__(path_self, mode, encoding, *args, **kwargs)

        def exists_patch(path_self):
            if path_self == playlist_path:
                return exists
            return track_exists_map.get(str(path_self), Path.exists(path_self))

        monkeypatch.setattr(Path, "open", open_patch)
        monkeypatch.setattr(Path, "exists", exists_patch)

        # Use the helper for content generation
        content = generate_playlist_content(lines, is_extm3u=is_extm3u, title=title)
        playlist_content.write(content)
        playlist_content.seek(0)
        yield playlist_content, playlist_path

    return _factory


def make_mock_file(suffix, name, parent_dir, is_file=True, is_relative_to_val=None, resolved_to=None):
    """Create MagicMock Path for scan_media_files tests."""
    p = MagicMock(spec=Path)
    p.suffix = suffix
    p.is_file.return_value = is_file
    p.resolve.return_value = resolved_to if resolved_to is not None else p
    p.name = name
    p.exists.return_value = True
    if is_relative_to_val is None:
        p.is_relative_to.side_effect = lambda other: False
    else:
        p.is_relative_to.side_effect = lambda other: is_relative_to_val
    p.__str__.return_value = str(parent_dir / name)
    p.parent = parent_dir
    return p


# --- Tests ---


class TestScanMediaFiles:
    """Test scan_media_files with various file scenarios."""

    @pytest.mark.parametrize(
        "playlist_dir, audio_files, playlist_files, expected_audio, expected_playlists",
        [
            ("music", ["track1.mp3", "track2.flac", "track3.ogg"], ["list1.m3u", "list2.m3u8", "list3.pls", "list4.log"], 3, 3),
            ("music/playlists", ["track1.mp3", "track2.flac", "track3.ogg", "note.txt"], ["list1.m3u", "list2.m3u8", "list3.pls", "list4.log"], 3, 3),
            ("music/playlists", ["track1.mp3", "list1.m3u", "note.txt", "track2.ogg"], ["list2.m3u8", "list3.pls"], 2, 2),
            ("other", ["track1.mp3", "track2.ogg", "list3.pls"], ["note.txt"], 2, 0),
            ("other", ["track1.mp3", "note.txt", "track2.ogg"], ["list1.m3u", "list2.mp3", "list3.pls"], 2, 2),
            ("music/playlists", ["note.txt", "readme.md"], ["list1.m3u", "list2.log"], 0, 1),
            ("other", [], [], 0, 0),
        ],
        ids=[
            "roots same, all audio and playlist types",
            "subdir, playlists only in playlist_root",
            "subdir, playlists in both roots",
            "disjoint, playlists only in audio_root",
            "disjoint, playlists only in playlist_root",
            "non-audio in audio_root, playlist in playlist_root",
            "empty dirs",
        ],
    )
    def test_scan_media_files_various(self, fsp_instance, tmp_path, playlist_dir, audio_files, playlist_files, expected_audio, expected_playlists):
        """Test scan_media_files for multiple scenarios."""
        audio_root = fsp_instance.path = tmp_path / "music"
        playlist_root = fsp_instance.playlist_path = tmp_path / playlist_dir

        dummy_files = []
        for filename in set(audio_files):
            is_relative = playlist_root.is_relative_to(audio_root)
            dummy_files.append(make_mock_file(Path(filename).suffix, filename, audio_root, is_file=True, is_relative_to_val=is_relative))
        for filename in set(playlist_files):
            dummy_files.append(make_mock_file(Path(filename).suffix, filename, playlist_root, is_file=True, is_relative_to_val=True))

        def rglob_side_effect(self, pattern):
            if str(self) == str(audio_root):
                if str(audio_root) == str(playlist_root):
                    return [f for f in dummy_files if str(f.parent) in {str(audio_root), str(playlist_root)}]
                elif playlist_root.is_relative_to(audio_root):
                    audio = [f for f in dummy_files if str(f.parent) == str(audio_root) and f.suffix.lower() in fsp_instance.AUDIO_EXT]
                    playlists = [f for f in dummy_files if str(f.parent) == str(playlist_root) and f.suffix.lower() in fsp_instance.PLAYLIST_EXT]
                    return audio + playlists
                else:
                    return [f for f in dummy_files if str(f.parent) == str(audio_root)]
            elif str(self) == str(playlist_root):
                if str(audio_root) == str(playlist_root):
                    return [f for f in dummy_files if str(f.parent) in {str(audio_root), str(playlist_root)}]
                else:
                    return [f for f in dummy_files if str(f.parent) == str(playlist_root)]
            else:
                return []

        with patch.object(Path, "rglob", rglob_side_effect):
            fsp_instance.scan_media_files()

        assert len(fsp_instance.get_tracks()) == expected_audio
        assert len(fsp_instance._get_playlist_paths()) == expected_playlists

    def test_scan_media_files_skips_duplicate_resolved_files(self, fsp_instance):
        """Test deduplication of resolved files."""
        audio_root = fsp_instance.path
        audio_root.mkdir(parents=True, exist_ok=True)
        resolved_path = audio_root / "track1.mp3"
        mock1 = make_mock_file(".mp3", "track1.mp3", audio_root, is_file=True, resolved_to=resolved_path)
        mock2 = make_mock_file(".mp3", "alias_track1.mp3", audio_root, is_file=True, resolved_to=resolved_path)
        with patch.object(Path, "rglob", return_value=[mock1, mock2]):
            fsp_instance.scan_media_files()
        audio_files = [t for t in fsp_instance._media_files if t.suffix.lower() in fsp_instance.AUDIO_EXT]
        assert len(audio_files) == 1


class ConfigurableFakeHandler:
    """Fake handler for FileSystemProvider tests."""

    def __init__(self, can_handle_return=True, can_handle_exception=None, apply_tags_return=None, apply_tags_exception=None):
        self.can_handle_return = can_handle_return
        self.can_handle_exception = can_handle_exception
        self.apply_tags_return = apply_tags_return
        self.apply_tags_exception = apply_tags_exception
        self.apply_tags_called = False
        self.can_handle_called = False
        self.last_apply_tags_args = None
        self.last_can_handle_args = None

    def can_handle(self, f):
        self.can_handle_called = True
        self.last_can_handle_args = (f,)
        if self.can_handle_exception:
            raise self.can_handle_exception
        return self.can_handle_return

    def apply_tags(self, audio_file, audio_tag, rating):
        self.apply_tags_called = True
        self.last_apply_tags_args = (audio_file, audio_tag, rating)
        if self.apply_tags_exception:
            raise self.apply_tags_exception
        return self.apply_tags_return


class TestUpdateTrackMetadata:
    """Test update_track_metadata for all handler branches."""

    @pytest.mark.parametrize(
        "audio_tag,rating,expect_none,audio_file_is_none,save_fails",
        [
            (AudioTag(), Rating(0.8), False, False, False),
            (AudioTag(), None, False, False, False),
            (None, Rating(0.8), False, False, False),
            (None, None, True, False, False),
            (AudioTag(), Rating(0.8), True, True, False),
            (AudioTag(), Rating(0.8), True, False, True),
        ],
    )
    def test_update_track_metadata_handler_succeeds(self, fsp_instance, file_factory, audio_tag, rating, expect_none, audio_file_is_none, save_fails):
        """Test handler success for all combinations of audio_tag and rating."""
        audio_file = file_factory(".mp3", "fake.mp3")
        file_path = Path(audio_file.filename)
        handler = ConfigurableFakeHandler(
            can_handle_return=True,
            apply_tags_return=audio_file,
        )
        fsp_instance.id3_handler = handler
        fsp_instance.vorbis_handler.can_handle = lambda f: False
        fsp_instance._handlers = [fsp_instance.id3_handler, fsp_instance.vorbis_handler]
        with patch.object(Path, "exists", return_value=True):
            with patch.object(fsp_instance, "_open_audio_file", return_value=None if audio_file_is_none else audio_file):
                audio_file.save = MagicMock(return_value=None)
                with patch.object(fsp_instance, "_save_audio_file", return_value=not save_fails):
                    result = fsp_instance.update_track_metadata(file_path, audio_tag=audio_tag, rating=rating)

        expected_result = None if expect_none else audio_file
        expected_apply_tags_called = not audio_file_is_none and not (audio_tag is None and rating is None)

        assert result is expected_result
        assert handler.apply_tags_called == expected_apply_tags_called

    def test_update_track_metadata_handler_not_supported(self, fsp_instance, file_factory):
        """Test handler not supporting file."""
        audio_file = file_factory(".mp3", "fake.mp3")
        file_path = Path(audio_file.filename)
        handler = ConfigurableFakeHandler(
            can_handle_return=False,
            apply_tags_return=audio_file,
        )
        fsp_instance.id3_handler = handler
        fsp_instance.vorbis_handler.can_handle = lambda f: False
        fsp_instance._handlers = [fsp_instance.id3_handler, fsp_instance.vorbis_handler]
        with patch.object(Path, "exists", return_value=True):
            with patch.object(fsp_instance, "_open_audio_file", return_value=audio_file):
                audio_file.save = MagicMock(return_value=None)
                result = fsp_instance.update_track_metadata(file_path, audio_tag=AudioTag(), rating=Rating(0.8))
        assert result is None
        assert not handler.apply_tags_called
        assert not audio_file.save.called

    def test_update_track_metadata_file_not_exists(self, fsp_instance, file_factory):
        """Test file does not exist."""
        audio_file = file_factory(".mp3", "fake.mp3")
        file_path = Path(audio_file.filename)
        handler = ConfigurableFakeHandler(
            can_handle_return=True,
            apply_tags_return=audio_file,
        )
        fsp_instance.id3_handler = handler
        fsp_instance.vorbis_handler.can_handle = lambda f: False
        fsp_instance._handlers = [fsp_instance.id3_handler, fsp_instance.vorbis_handler]
        with patch.object(Path, "exists", return_value=False):
            with patch.object(fsp_instance, "_open_audio_file", return_value=audio_file):
                audio_file.save = MagicMock(return_value=None)
                result = fsp_instance.update_track_metadata(file_path, audio_tag=AudioTag(), rating=Rating(0.8))
        assert result is None
        assert not handler.apply_tags_called
        assert not audio_file.save.called

    def test_update_track_metadata_unsupported_filetype(self, fsp_instance, file_factory):
        """Test unsupported file type."""
        audio_file = file_factory(".nonsense", "fake.nonsense")
        file_path = Path(audio_file.filename)
        handler = ConfigurableFakeHandler(
            can_handle_return=False,
            apply_tags_return=audio_file,
        )
        fsp_instance.id3_handler = handler
        fsp_instance.vorbis_handler.can_handle = lambda f: False
        fsp_instance._handlers = [fsp_instance.id3_handler, fsp_instance.vorbis_handler]
        with patch.object(Path, "exists", return_value=True):
            with patch.object(fsp_instance, "_open_audio_file", return_value=audio_file):
                audio_file.save = MagicMock(return_value=None)
                result = fsp_instance.update_track_metadata(file_path, audio_tag=AudioTag(), rating=Rating(0.8))
        assert result is None
        assert not handler.apply_tags_called
        assert not audio_file.save.called


class TestFinalizeScan:
    """Test finalize_scan for small and large deferred_tracks."""

    @pytest.mark.parametrize("count,use_bar", [(3, False), (150, True)])
    def test_finalize_scan_small_vs_large(self, fsp_instance, count, use_bar):
        dummy_handler = MagicMock(can_handle=lambda f: True)
        fsp_instance._handlers = [dummy_handler]
        fsp_instance.deferred_tracks = [{"track": AudioTag(ID=str(i), title="t", artist="a", album="b", track=1), "handler": dummy_handler, "raw": {}} for i in range(count)]
        fsp_instance.update_track_metadata = MagicMock()

        result = fsp_instance.finalize_scan()
        assert len(result) == count
        if use_bar:
            fsp_instance.status_mgr.start_phase.assert_called()
        else:
            fsp_instance.status_mgr.start_phase.assert_not_called()
        assert fsp_instance.update_track_metadata.call_count == count


class TestPlaylistCreation:
    """Test create_playlist for dry-run, mkdir error, open error, and success."""

    @pytest.mark.parametrize("is_extm3u", [True, False])
    def test_create_playlist_success(self, fsp_instance, is_extm3u):
        fsp_instance.config_mgr.dry = False
        result = fsp_instance.create_playlist("pl", is_extm3u=is_extm3u)
        assert isinstance(result, Playlist)
        assert result.is_extm3u == is_extm3u

    def test_create_playlist_dry_run(self, fsp_instance):
        fsp_instance.config_mgr.dry = True
        result = fsp_instance.create_playlist("pl", is_extm3u=True)
        assert result is None

    def test_create_playlist_mkdir_fail(self, fsp_instance):
        fsp_instance.config_mgr.dry = False
        with patch.object(Path, "mkdir", side_effect=OSError()):
            result = fsp_instance.create_playlist("pl", is_extm3u=True)
        assert result is None

    def test_create_playlist_write_fail(self, fsp_instance):
        fsp_instance.config_mgr.dry = False
        with patch("pathlib.Path.open", side_effect=OSError()):
            result = fsp_instance.create_playlist("pl", is_extm3u=True)
        assert result is None


class TestReadTrackMetadata:
    """Test reading track metadata for supported and unsupported formats."""

    @pytest.mark.parametrize(
        "file_args,handler_attr,expect",
        [
            ((".mp3", "fake.mp3"), "id3_handler", True),
            ((".flac", "fake.flac"), "vorbis_handler", True),
            ((".ogg", "fake.ogg"), "vorbis_handler", True),
            ((".txt", "fake.txt"), None, False),
            ((".nonsense", "fake.nonsense"), None, False),
        ],
    )
    def test_read_track_metadata_dispatch(self, fsp_instance, file_factory, file_args, handler_attr, expect):
        audio_file = file_factory(*file_args)
        file_path = Path(audio_file.filename)
        if handler_attr:
            handler = getattr(fsp_instance, handler_attr)
            handler.can_handle = lambda f: True
            handler.read_tags = lambda f: (AudioTag(ID="x"), None)
        for h in [fsp_instance.id3_handler, fsp_instance.vorbis_handler]:
            if not handler_attr or h is not getattr(fsp_instance, handler_attr, None):
                h.can_handle = lambda f: False
                h.read_tags = lambda f: (None, None)
        with patch.object(fsp_instance, "_open_audio_file", return_value=audio_file):
            original_deferred = list(fsp_instance.deferred_tracks)
            result = fsp_instance.read_track_metadata(file_path)
        if expect:
            assert isinstance(result, AudioTag)
        else:
            assert result is None
        assert fsp_instance.deferred_tracks == original_deferred

    def test_read_track_metadata_open_error(self, fsp_instance):
        with patch("mutagen.File", return_value=None):
            assert fsp_instance.read_track_metadata(Path("x.mp3")) is None

    def test_read_track_metadata_deferred(self, fsp_instance, file_factory):
        audio_file = file_factory(".mp3", "fake.mp3")
        file_path = Path(audio_file.filename)
        fsp_instance.id3_handler.can_handle = lambda f: True
        fsp_instance.vorbis_handler.can_handle = lambda f: False
        fsp_instance.id3_handler.read_tags = lambda f: (AudioTag(ID="x"), {"TXXX:RATING": "5"})
        with patch.object(fsp_instance, "_open_audio_file", return_value=audio_file):
            fsp_instance.deferred_tracks = []
            result = fsp_instance.read_track_metadata(file_path)
        assert isinstance(result, AudioTag)
        assert len(fsp_instance.deferred_tracks) == 1


class TestReadPlaylistMetadata:
    """Test read_playlist_metadata for various playlist content."""

    @pytest.mark.parametrize(
        "lines,is_extm3u,title,expect_none,expected_name",
        [
            (["music/track1.mp3"], False, None, False, "x"),
            (["music/track1.mp3"], True, None, False, "x"),
            (["#PLAYLIST:MyList", "music/track1.mp3"], True, "MyList", False, "MyList"),
            (["#EXTM3U", "# This is a comment", ""], True, None, False, "x"),
        ],
    )
    def test_read_playlist_metadata_variations(self, fsp_instance, playlist_factory, lines, is_extm3u, title, expect_none, expected_name, monkeypatch):
        with playlist_factory(
            playlist_root=fsp_instance.playlist_path,
            filename="x.m3u",
            lines=lines,
            is_extm3u=is_extm3u,
            title=title,
            monkeypatch=monkeypatch,
        ) as (playlist_content, playlist_path):
            result = fsp_instance.read_playlist_metadata(playlist_path)
            if expect_none:
                assert result is None
            else:
                assert isinstance(result, Playlist)
                assert result.is_extm3u == is_extm3u
                assert result.name == expected_name

    def test_open_playlist_file_not_exists_in_read_mode(self, fsp_instance, playlist_factory, monkeypatch):
        with playlist_factory(
            playlist_root=fsp_instance.playlist_path,
            filename="notfound.m3u",
            lines=["music/track1.mp3"],
            exists=False,
            monkeypatch=monkeypatch,
        ) as (playlist_content, playlist_path):
            result = fsp_instance._open_playlist(playlist_path, mode="r")
            assert result is None

    def test_open_playlist_open_raises_exception(self, fsp_instance, playlist_factory, monkeypatch):
        with playlist_factory(
            playlist_root=fsp_instance.playlist_path,
            filename="failopen.m3u",
            lines=["music/track1.mp3"],
            open_raises=True,
            monkeypatch=monkeypatch,
        ) as (playlist_content, playlist_path):
            result = fsp_instance._open_playlist(playlist_path, mode="r")
            assert result is None


def _build_path_maps(playlist_case, playlist_path):
    exists_map = {}
    scope_map = {}
    for entry in playlist_case:
        line = entry["line"]
        if "exists" in entry and line and not line.strip().startswith("#"):
            candidate_path = (playlist_path.parent / line) if not Path(line).is_absolute() else Path(line)
            exists_map[str(candidate_path)] = entry["exists"]
        if "in_scope" in entry and line and not line.strip().startswith("#"):
            candidate_path = (playlist_path.parent / line) if not Path(line).is_absolute() else Path(line)
            scope_map[str(candidate_path)] = entry["in_scope"]
    return exists_map, scope_map


def _expected_tracks(playlist_case, playlist_path):
    expected = set()
    for entry in playlist_case:
        line = entry["line"]
        if "exists" in entry and "in_scope" in entry and entry["exists"] and entry["in_scope"]:
            candidate_path = (playlist_path.parent / line) if not Path(line).is_absolute() else Path(line)
            expected.add(str(candidate_path.resolve()))
    return expected


class TestGetTracksFromPlaylist:
    """Test get_tracks_from_playlist for filtering of comments, blank, in-scope, and out-of-scope lines."""

    def test_get_tracks_from_playlist(self, fsp_instance, playlist_factory, monkeypatch):
        audio_root = fsp_instance.path
        playlist_case = [
            {"line": "music/track1.mp3", "exists": True, "in_scope": True},
            {"line": "music/track2.mp3", "exists": False, "in_scope": True},
            {"line": "other/track3.mp3", "exists": True, "in_scope": False},
            {"line": "other/track4.mp3", "exists": False, "in_scope": False},
            {"line": " "},
            {"line": str((audio_root / "abs_track1.mp3").absolute()), "exists": True, "in_scope": True},
            {"line": str((audio_root / "abs_track2.mp3").absolute()), "exists": False, "in_scope": True},
            {"line": str((audio_root.parent / "other" / "abs_track3.mp3").absolute()), "exists": True, "in_scope": False},
            {"line": str((audio_root.parent / "other" / "abs_track4.mp3").absolute()), "exists": False, "in_scope": False},
            {"line": "# This is a comment"},
        ]
        lines = [entry["line"] for entry in playlist_case]
        with playlist_factory(
            playlist_root=fsp_instance.playlist_path,
            filename="test_playlist.m3u",
            lines=lines,
            monkeypatch=monkeypatch,
        ) as (playlist_content, playlist_path):
            exists_map, scope_map = _build_path_maps(playlist_case, playlist_path)
            real_exists = Path.exists
            real_is_relative_to = Path.is_relative_to

            def exists_side_effect(self):
                if self == playlist_path:
                    return real_exists(self)
                return exists_map.get(str(self), False)

            def is_relative_to_side_effect(self, other):
                if self == playlist_path:
                    return real_is_relative_to(self, other)
                return scope_map.get(str(self), False)

            with patch.object(Path, "exists", exists_side_effect), patch.object(Path, "is_relative_to", is_relative_to_side_effect):
                result = fsp_instance.get_tracks_from_playlist(playlist_path)
            result_paths = {str(Path(p).resolve()) for p in result}
            expected = _expected_tracks(playlist_case, playlist_path)
            assert result_paths == expected

    def test_get_tracks_from_playlist_only_comments_and_blanks(self, fsp_instance, playlist_factory, monkeypatch):
        playlist_case = [
            {"line": "#c"},
            {"line": ""},
            {"line": "# another comment"},
            {"line": ""},
        ]
        lines = [entry["line"] for entry in playlist_case]
        with playlist_factory(
            playlist_root=fsp_instance.playlist_path,
            filename="test_playlist.m3u",
            lines=lines,
            monkeypatch=monkeypatch,
        ) as (playlist_content, playlist_path):
            with patch.object(Path, "exists", return_value=True), patch.object(Path, "is_relative_to", return_value=True):
                result = fsp_instance.get_tracks_from_playlist(playlist_path)
            assert result == []


class TestAddTrackToPlaylist:
    """Test add_track_to_playlist for success and file open error."""

    @pytest.mark.parametrize("is_extm3u", [True, False])
    def test_add_track_to_playlist_success(self, is_extm3u, fsp_instance, playlist_factory, monkeypatch):
        track1_path = fsp_instance.path / "test_track1.mp3"
        track1 = AudioTag(file_path=str(track1_path), artist="A", title="T", duration=123)
        with playlist_factory(
            playlist_root=fsp_instance.playlist_path,
            filename="test_playlist.m3u",
            lines=["#EXTM3U", "# This is a comment", "music/track1.mp3"],
            is_extm3u=is_extm3u,
            monkeypatch=monkeypatch,
        ) as (playlist_content, playlist_path):
            playlist_content.seek(0)
            original_content = playlist_content.read()
            assert str(track1_path) not in original_content

            fsp_instance.add_track_to_playlist(playlist_path, track1, is_extm3u=is_extm3u)
            playlist_content.seek(0)
            content = playlist_content.read()
            assert any("#EXTINF:123,A - T" in call for call in playlist_content.write_calls) == is_extm3u
            assert any("test_track1.mp3" in call for call in playlist_content.write_calls)
            assert ("#EXTINF:123,A - T" in content) == is_extm3u
            assert "test_track1.mp3" in content

    def test_add_track_to_playlist_file_open_error(self, fsp_instance, tmp_path):
        playlist_path = tmp_path / "fail.m3u"
        track_path = fsp_instance.path / "test_track1.mp3"
        track = AudioTag(file_path=str(track_path), artist="A", title="T", duration=123)
        with patch.object(Path, "is_relative_to", return_value=True), patch.object(Path, "open", side_effect=OSError("Simulated file open error")):
            fsp_instance.add_track_to_playlist(playlist_path, track, is_extm3u=True)

    def test_add_track_to_playlist_outside_audio_root_no_write(self, fsp_instance, tmp_path):
        playlist_path = tmp_path / "test_playlist.m3u"
        outside_path = tmp_path.parent / "outside.mp3"
        track = AudioTag(file_path=str(outside_path), artist="X", title="Y", duration=100)
        with (
            patch.object(Path, "is_relative_to", return_value=False),
            patch.object(Path, "open", side_effect=AssertionError("Should not open file for outside track")),
            patch.object(fsp_instance.logger, "debug"),
        ):
            fsp_instance.add_track_to_playlist(playlist_path, track, is_extm3u=True)


class TestRemoveTrackFromPlaylist:
    """Test remove_track_from_playlist for success and file open error."""

    @pytest.mark.parametrize(
        "initial_lines,remove_track,expected_lines",
        [
            (["music/track1.mp3", "music/track2.mp3"], "music/track1.mp3", ["music/track2.mp3"]),
            (["music/track1.mp3", "music/track2.mp3"], "music/track2.mp3", ["music/track1.mp3"]),
            (["music/track1.mp3"], "music/track1.mp3", []),
            (["music/track1.mp3", "music/track2.mp3"], "music/track3.mp3", ["music/track1.mp3", "music/track2.mp3"]),
        ],
    )
    def test_remove_track_from_playlist_success(self, fsp_instance, playlist_factory, initial_lines, remove_track, expected_lines, monkeypatch):
        with playlist_factory(
            playlist_root=fsp_instance.playlist_path,
            filename="test_playlist.m3u",
            lines=initial_lines,
            monkeypatch=monkeypatch,
        ) as (playlist_content, playlist_path):
            playlist_content.seek(0)
            original_content = playlist_content.read()

            track = playlist_path.parent / remove_track
            fsp_instance.remove_track_from_playlist(playlist_path, track)
            playlist_content.seek(0)
            new_content = playlist_content.read()
            written_lines = [str(Path(line.strip())) for lines in playlist_content.writelines_calls for line in lines if line.strip()]
            expected_lines_stripped = [str(Path(line.strip())) for line in expected_lines if line.strip()]
            assert written_lines == expected_lines_stripped or (not written_lines and not expected_lines_stripped)
            if remove_track in initial_lines:
                assert original_content != new_content
            else:
                assert original_content == new_content

    def test_remove_track_from_playlist_file_open_error(self, fsp_instance, tmp_path):
        playlist_path = tmp_path / "fail.m3u"
        track_path = fsp_instance.path / "test_track1.mp3"
        with patch.object(Path, "is_relative_to", return_value=True), patch.object(Path, "open", side_effect=OSError("Simulated file open error")):
            fsp_instance.remove_track_from_playlist(playlist_path, track_path)


class TestGetPlaylistTitle:
    """Test _get_playlist_title for disambiguation when all folders are exhausted."""

    def test_get_playlist_title_duplicate_success(self, fsp_instance, tmp_path):
        pl_path = tmp_path / "a" / "b" / "MyList.m3u"
        pl_path.parent.mkdir(parents=True)
        fsp_instance.playlist_path = tmp_path
        fsp_instance._playlist_title_map["MyList"] = pl_path.parent / "other" / "MyList.m3u"
        title = fsp_instance._get_playlist_title(pl_path, "MyList")
        assert ".MyList" in title

    def test_get_playlist_title_exhausted_warns(self, fsp_instance):
        fsp_instance = FileSystemProvider()
        fsp_instance.logger = MagicMock()
        fsp_instance.playlist_path = Path("/root")
        pl_path = Path("/root/MyList.m3u")
        fsp_instance._playlist_title_map = {"MyList": Path("/other/SomeOtherList.m3u")}
        result = fsp_instance._get_playlist_title(pl_path, "MyList")
        assert result == "MyList"
        fsp_instance.logger.warning.assert_called_once()


class TestReadPlaylistMetadataOpenError:
    """Test read_playlist_metadata for file open failure."""

    def test_read_playlist_metadata_open_error(self, fsp_instance, tmp_path):
        pl_path = tmp_path / "fail.m3u"
        with patch.object(fsp_instance, "_open_playlist", return_value=None):
            result = fsp_instance.read_playlist_metadata(pl_path)
        assert result is None


class TestGetPlaylistsFiltering:
    """Test get_playlists filtering by title and path."""

    CASES = [
        ({}, {"Rock", "Pop", "Jazz"}, set(), "no_filter"),
        ({"title": "Pop"}, {"Pop"}, set(), "by_title_hit"),
        ({"title": "Classical"}, set(), set(), "by_title_miss"),
        ({"path": "/fake/rock.m3u"}, {"Rock"}, set(), "by_path_hit"),
        ({"path": "/does/not/exist.m3u"}, set(), set(), "by_path_miss"),
        ({"title": ""}, {"Rock", "Pop", "Jazz"}, set(), "empty_title_treated_as_none"),
        ({"title": None}, {"Rock", "Pop", "Jazz"}, set(), "title_None"),
        ({}, {"Rock", "Jazz"}, {"Pop"}, "skip_pop_none"),
    ]

    @pytest.mark.parametrize("filter_kwargs, expected, skip_titles, _id", CASES, ids=[c[3] for c in CASES])
    def test_get_playlists_filtering(self, fsp_instance, filter_kwargs, expected, skip_titles, _id):
        def _register(fsp, title: str, fake_path: str) -> Path:
            p = Path(fake_path).resolve()
            fsp._media_files.append(p)
            fsp._playlist_title_map[title] = p
            return p

        _register(fsp_instance, "Rock", "/fake/rock.m3u")
        _register(fsp_instance, "Pop", "/fake/pop.m3u")
        _register(fsp_instance, "Jazz", "/fake/jazz.m3u")

        title_by_path = {v: k for k, v in fsp_instance._playlist_title_map.items()}

        def stub_reader(path: Path):
            resolved = Path(path).resolve()
            title = title_by_path.get(resolved, "UNKNOWN")
            if title in skip_titles:
                return None
            return Playlist(ID=str(resolved), name=title)

        fsp_instance.read_playlist_metadata = MagicMock(side_effect=stub_reader)
        result_titles = {pl.name for pl in fsp_instance.get_playlists(**filter_kwargs)}
        assert result_titles == expected

    def test_get_playlists_raises_on_both_title_and_path(self, fsp_instance):
        path_rock = Path("/fake/rock.m3u").resolve()
        fsp_instance._media_files.append(path_rock)
        fsp_instance._playlist_title_map["Rock"] = path_rock
        fsp_instance.read_playlist_metadata = MagicMock(return_value=Playlist(ID=str(path_rock), name="Rock"))

        with pytest.raises(ValueError):
            fsp_instance.get_playlists(title="Rock", path=path_rock)


class TestOpenAudioFile:
    """Test _open_audio_file error and exception branches."""

    def test_open_audio_file_returns_none_raises_valueerror(self, fsp_instance):
        with patch("filesystem_provider.mutagen.File", return_value=None):
            result = fsp_instance._open_audio_file(Path("fake.mp3"))
            assert result is None

    def test_open_audio_file_mutagen_raises_returns_none(self, fsp_instance):
        with patch("filesystem_provider.mutagen.File", side_effect=RuntimeError("fail")):
            result = fsp_instance._open_audio_file(Path("fake.mp3"))
            assert result is None

    def test_open_audio_file_success(self, fsp_instance):
        audio_file = MagicMock()
        audio_file.__bool__.return_value = True
        with patch("filesystem_provider.mutagen.File", return_value=audio_file):
            result = fsp_instance._open_audio_file(Path("fake.mp3"))
        assert result is audio_file


class TestSaveAudioFile:
    """Test _save_audio_file for dry-run, ID3FileType, and exception."""

    def test_save_audio_file_dry_returns_true(self, fsp_instance):
        fsp_instance.config_mgr.dry = True
        audio_file = MagicMock()
        result = fsp_instance._save_audio_file(audio_file)
        assert result is True
        assert not audio_file.save.called

    def test_save_audio_file_id3filetype_calls_save_and_returns_true(self, fsp_instance):
        dummy = MagicMock(spec=ID3FileType)
        dummy.filename = "fake.mp3"
        result = fsp_instance._save_audio_file(dummy)
        assert result is True
        dummy.save.assert_called_once()

    def test_save_audio_file_save_raises_returns_false(self, fsp_instance):
        audio_file = MagicMock()
        audio_file.save.side_effect = Exception("fail")
        result = fsp_instance._save_audio_file(audio_file)
        assert result is False
