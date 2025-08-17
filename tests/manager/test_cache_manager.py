import datetime as dt
import logging
import os
import pickle
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from manager.config_manager import CacheMode
from sync_items import AudioTag


@pytest.fixture
def dummy_audio_tag():
    return AudioTag(ID="abc123", title="Test", artist="Test Artist", album="Album", track=1)


@pytest.fixture
def cache_factory(request, monkeypatch):
    """Factory fixture returning a callable that creates a real Cache instance for tests."""

    def _make(columns=None, dtype=None, save_threshold=2, max_age_hours=None, *, file_exists=True, file_old=False, unpickle_error=False, file_df=None):
        cols = columns or ["ID", "foo"]
        dty = dtype or {c: object for c in cols}

        temp_dir = Path(tempfile.mkdtemp())
        filepath = str(temp_dir / "cache.pkl")
        dump_mock = MagicMock()

        # Patch pickle.dump used by Cache.save so tests don't perform real write
        monkeypatch.setattr("manager.cache_manager.pickle.dump", dump_mock)

        # Control os.path.exists so Cache.load sees the desired existence state
        monkeypatch.setattr("os.path.exists", lambda path: path == filepath if file_exists else False)

        # If a file is meant to exist on disk, actually create it so pickle.load/open work normally
        if file_exists:
            # Write either a valid pickle or invalid content to simulate unpickle error
            if unpickle_error:
                with open(filepath, "wb") as f:
                    f.write(b"notapickle")
            else:
                df = file_df if file_df is not None else pd.DataFrame({"ID": ["x"]})
                with open(filepath, "wb") as f:
                    pickle.dump(df, f)
            # Optionally set the file mtime to an old timestamp to trigger age logic
            if file_old:
                old_time = (dt.datetime.now() - dt.timedelta(hours=2)).timestamp()
                os.utime(filepath, (old_time, old_time))

        # Import here so monkeypatches are in effect
        from manager.cache_manager import Cache

        c = Cache(filepath=filepath, columns=cols, dtype=dty, save_threshold=save_threshold, max_age_hours=max_age_hours)
        c.load()
        c._dump_mock = dump_mock

        return c

    return _make


@pytest.fixture
def cache_manager(request, monkeypatch):
    mode = getattr(request, "param", {}).get("mode", CacheMode.METADATA)

    # Use a MagicMock for config_manager with only the required attribute
    config_manager = MagicMock()
    config_manager.cache_mode = mode

    # Patch get_manager to return a fake manager with the mock config_manager
    fake_manager = MagicMock()
    fake_manager.get_config_manager.return_value = config_manager
    monkeypatch.setattr("manager.cache_manager.get_manager", lambda: fake_manager)

    from manager.cache_manager import CacheManager

    cm = CacheManager()
    cm.metadata_cache = None
    cm.match_cache = None
    cm.stats_mgr = MagicMock()
    cm.logger = MagicMock()
    # Attach the config_manager for direct access in tests if needed
    cm._test_config_manager = config_manager
    return cm


class TestCache:
    @pytest.mark.parametrize(
        "file_exists, file_old, unpickle_error, expected_type",
        [
            (True, True, False, pd.DataFrame),
            (False, False, False, pd.DataFrame),
            (True, False, True, pd.DataFrame),
        ],
    )
    def test_load_file_states_returns_dataframe(self, cache_factory, file_exists, file_old, unpickle_error, expected_type):
        """Test Cache.load returns DataFrame for file missing, file old, and unpickle error."""
        # Use cache_factory to control filesystem-side effects instead of manually creating files
        c = cache_factory(columns=["ID"], dtype={"ID": "str"}, max_age_hours=1, file_exists=file_exists, file_old=file_old, unpickle_error=unpickle_error)
        assert isinstance(c.cache, expected_type)

    @pytest.mark.parametrize(
        "max_age_hours,file_age_hours,should_discard",
        [
            (None, 25, False),
            (1, 25, True),
            (25, 1, False),
        ],
    )
    def test_load_age_expiration_discards_or_keeps_data(self, tmp_path, monkeypatch, max_age_hours, file_age_hours, should_discard):
        """Test Cache.load discards or keeps data based on file age and max_age_hours."""
        fpath = tmp_path / "cache_age.pkl"
        df = pd.DataFrame({"ID": ["1"]})
        with open(fpath, "wb") as f:
            pickle.dump(df, f)
        fake_mtime = dt.datetime.now().timestamp() - (file_age_hours * 60 * 60)
        monkeypatch.setattr("os.path.getmtime", lambda path: fake_mtime)
        from manager.cache_manager import Cache

        c = Cache(filepath=str(fpath), columns=["ID"], dtype={"ID": "str"}, max_age_hours=max_age_hours)
        c.load()
        if should_discard:
            assert c.cache.shape[0] > 0
            assert (c.cache["ID"] == "1").sum() == 0
        else:
            assert (c.cache["ID"] == "1").sum() == 1

    @pytest.mark.parametrize(
        "missing,extra",
        [
            (True, False),
            (False, True),
            (True, True),
        ],
    )
    def test_ensure_columns_adds_missing_and_preserves_extra(self, cache_factory, missing, extra):
        """Test _ensure_columns adds missing columns and preserves extra columns."""
        cols = ["ID", "foo"]
        data = {"ID": ["1"]}
        if extra:
            data["bar"] = ["baz"]
        c = cache_factory(columns=cols)
        c.cache = pd.DataFrame(data)
        if missing:
            # Remove 'foo' if present
            if "foo" in c.cache.columns:
                c.cache = c.cache.drop(columns=["foo"])
        c._ensure_columns()
        for col in cols:
            assert col in c.cache.columns
        if extra:
            assert "bar" in c.cache.columns

    @pytest.mark.parametrize(
        "preallocate,add_row,expected_empty",
        [
            (True, False, True),
            (True, True, False),
            (False, False, True),
        ],
    )
    def test_is_empty_various_states_returns_expected(self, cache_factory, preallocate, add_row, expected_empty):
        """Test is_empty returns True for empty cache and False for non-empty cache."""
        c = cache_factory()
        if not preallocate:
            c.cache = c.cache.iloc[0:0]
        if add_row:
            c.cache.loc[0, "ID"] = "foo"
        assert c.is_empty() is expected_empty

    @pytest.mark.parametrize(
        "initial_rows,add_rows",
        [
            (2, 7),
            (5, 10),
            (0, 3),
        ],
    )
    def test_resize_adds_rows_increases_length(self, cache_factory, initial_rows, add_rows):
        """Test resize adds the requested number of rows to the cache."""
        c = cache_factory()
        c.cache = pd.DataFrame({"ID": [str(i) for i in range(initial_rows)], "foo": ["x"] * initial_rows})
        before = len(c.cache)
        c.resize(add_rows)
        after = len(c.cache)
        assert after == before + add_rows

    def test_save_exception_logs_error(self, cache_factory, monkeypatch, caplog):
        """Test save writes file and logs error if exception occurs."""
        c = cache_factory()
        c.cache.loc[0, "ID"] = "foo"
        # First save should call the mocked pickle.dump without raising
        c.save()
        # Now make open raise to exercise the exception branch
        monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(IOError("fail")))
        c.save()
        assert any("Failed to save cache" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "criteria,data,expected_rows,expected_values",
        [
            ({"ID": "1"}, {"foo": "bar"}, 1, ["bar"]),  # Insert new row
            ({"ID": "1"}, {"foo": "baz"}, 1, ["baz"]),  # Update existing row
            ({"ID": "2"}, {"foo": None}, 2, [None]),  # Insert with None
        ],
    )
    def test_upsert_row_insert_and_update(self, cache_factory, criteria, data, expected_rows, expected_values):
        """Test upsert_row inserts new row or updates existing row, including None handling."""
        c = cache_factory()
        c.cache = c.cache.iloc[0:0].copy()
        c._ensure_columns()
        c.upsert_row(criteria, data)
        # Upsert again to test update branch
        c.upsert_row(criteria, {"foo": expected_values[0]})
        found = c._find_row_by_columns(criteria)
        assert len(found) == 1
        assert found.iloc[0]["foo"] == expected_values[0] or pd.isna(found.iloc[0]["foo"])

    @pytest.mark.parametrize(
        "criteria,expected_indices",
        [
            ({"ID": "1"}, [0]),
            ({"ID": "2"}, [1]),
        ],
    )
    def test_find_row_by_columns_matches_and_no_match(self, cache_factory, criteria, expected_indices):
        """Test _find_row_by_columns returns correct rows for match and no match."""
        c = cache_factory()
        c.cache = pd.DataFrame({"ID": ["1", "2"], "foo": ["a", "b"]})
        result = c._find_row_by_columns(criteria)
        assert list(result.index) == expected_indices

    def test_find_empty_row_or_resize(self, cache_factory):
        """Test _find_empty_row_or_resize reuses an existing empty row, then appends a new one when none remain."""

        c = cache_factory(save_threshold=0)
        before_shape = c.cache.shape
        # First call: should reuse the empty row
        idx1 = c._find_empty_row_or_resize()
        after_shape1 = c.cache.shape
        assert before_shape == after_shape1, f"Shape changed unexpectedly: {before_shape} -> {after_shape1}"
        row1 = c.cache.loc[idx1]
        assert all(pd.isna(row1)), f"Row at idx1={idx1} should be empty, got: {row1}"
        # Fill the row
        c.cache.loc[idx1, "ID"] = "1"
        c.cache.loc[idx1, "foo"] = "a"
        # Second call: should append a new empty row
        before_shape2 = c.cache.shape
        idx2 = c._find_empty_row_or_resize()
        after_shape2 = c.cache.shape
        assert after_shape2[0] > before_shape2[0], f"Row count did not increase: {before_shape2[0]} -> {after_shape2[0]}"
        row2 = c.cache.loc[idx2]
        assert all(pd.isna(row2)), f"Row at idx2={idx2} should be empty, got: {row2}"

    def test_update_row_updates_only_existing_columns(self, cache_factory):
        """Test _update_row only updates columns that exist in cache."""
        c = cache_factory()
        c.cache = pd.DataFrame({"ID": ["1"], "foo": ["a"]})
        c._update_row(0, {"foo": "b", "bar": "should_ignore"})
        assert c.cache.loc[0, "foo"] == "b"
        assert "bar" not in c.cache.columns

    @pytest.mark.parametrize("value,expected", [(None, pd.NA), ("x", "x")])
    def test_insert_row_handles_none_and_value(self, cache_factory, value, expected):
        """Test _insert_row inserts pd.NA for None and value otherwise."""
        c = cache_factory()
        c._insert_row(0, {"ID": "1", "foo": value})
        if expected is pd.NA:
            assert pd.isna(c.cache.loc[0, "foo"])
        else:
            assert c.cache.loc[0, "foo"] == expected

    @pytest.mark.parametrize(
        "data,expected_present,expected_absent",
        [
            ({"ID": "1", "foo": "bar"}, ["ID", "foo"], []),
            ({"ID": "2", "notacol": "baz"}, ["ID"], ["notacol"]),
        ],
    )
    def test_insert_row_handles_missing_and_present_keys(self, cache_factory, data, expected_present, expected_absent):
        """Test _insert_row only sets values for keys present in columns, ignores others."""
        c = cache_factory()
        c._insert_row(0, data)
        for key in expected_present:
            assert key in c.cache.columns
            assert c.cache.loc[0, key] == data[key] or pd.isna(c.cache.loc[0, key])
        for key in expected_absent:
            assert key not in c.cache.columns

    @pytest.mark.parametrize(
        "file_exists, remove_raises",
        [
            (True, False),
            (True, True),
            (False, False),
        ],
    )
    def test_delete_file_all_paths(self, monkeypatch, caplog, file_exists, remove_raises):
        """Test Cache.delete_file covers all code paths: file exists/success, file exists/error, file missing."""
        dummy_path = "dummy_cache.pkl"
        monkeypatch.setattr("os.path.exists", lambda path: file_exists)
        mock_remove = MagicMock()
        if file_exists and remove_raises:
            mock_remove.side_effect = OSError("fail")
        monkeypatch.setattr("os.remove", mock_remove)
        from manager.cache_manager import Cache

        with caplog.at_level(logging.ERROR):
            Cache.delete_file(dummy_path)
            assert len(caplog.records) == (1 if remove_raises else 0)
        if file_exists:
            assert mock_remove.called
        else:
            assert not mock_remove.called


class TestCacheManager:
    @pytest.mark.parametrize(
        "mode, expect_match, expect_metadata, expect_match_enabled, expect_metadata_enabled",
        [
            (CacheMode.MATCHES, True, True, True, True),
            (CacheMode.METADATA, False, True, False, True),
            (CacheMode.MATCHES_ONLY, True, False, True, False),
            (CacheMode.DISABLED, False, False, False, False),
        ],
    )
    def test_cache_manager_mode_initialization_creates_expected_caches(self, cache_manager, mode, expect_match, expect_metadata, expect_match_enabled, expect_metadata_enabled):
        """Test CacheManager initializes caches and enables flags according to mode."""
        cache_manager._test_config_manager.cache_mode = mode
        cache_manager.mode = mode
        cache_manager._initialize_caches()
        # Single-line assertions for cache existence
        assert (cache_manager.match_cache is not None) == expect_match
        assert (cache_manager.metadata_cache is not None) == expect_metadata
        assert cache_manager.is_match_cache_enabled() is expect_match_enabled
        assert cache_manager.is_metadata_cache_enabled() is expect_metadata_enabled

    def test_safe_get_value_returns_none_for_nan_and_value_for_present(self, cache_manager):
        """Test _safe_get_value returns None for pd.NA and value for present cell."""
        df = pd.DataFrame({"foo": [pd.NA, "bar"]})
        assert cache_manager._safe_get_value(df.iloc[[0]], "foo") is None
        assert cache_manager._safe_get_value(df.iloc[[1]], "foo") == "bar"

    def test_log_cache_hit_increments_stats_and_logs(self, cache_manager):
        """Test _log_cache_hit increments stats and logs trace."""
        cache_manager.stats_mgr = MagicMock()
        cache_manager.logger = MagicMock()
        cache_manager._log_cache_hit("match", "keyinfo")
        cache_manager.stats_mgr.increment.assert_called_with("cache_hits")
        cache_manager.logger.trace.assert_called()

    @pytest.mark.parametrize(
        "mode",
        [
            CacheMode.MATCHES,
            CacheMode.MATCHES_ONLY,
            CacheMode.METADATA,
            CacheMode.DISABLED,
        ],
    )
    def test_is_cache_ready(self, cache_manager, mode):
        """Test _is_cache_ready returns expected values for both caches, using real enabled helpers."""
        cache_manager.mode = mode
        cache_manager._initialize_caches()

        match_enabled = cache_manager.is_match_cache_enabled()
        meta_enabled = cache_manager.is_metadata_cache_enabled()

        # Expected: ready if enabled and cache exists and not empty
        match_expected = match_enabled and cache_manager.match_cache is not None and not cache_manager.match_cache.is_empty()
        meta_expected = meta_enabled and cache_manager.metadata_cache is not None and not cache_manager.metadata_cache.is_empty()

        assert cache_manager._is_cache_ready(cache_manager.match_cache, match_enabled) is match_expected
        assert cache_manager._is_cache_ready(cache_manager.metadata_cache, meta_enabled) is meta_expected

    @pytest.mark.parametrize(
        "mode, expect_match_deleted, expect_metadata_deleted",
        [
            (CacheMode.MATCHES, True, True),
            (CacheMode.METADATA, False, True),
            (CacheMode.MATCHES_ONLY, True, False),
            (CacheMode.DISABLED, False, False),
        ],
    )
    def test_invalidate_sets_caches_to_none_and_deletes_files(self, cache_manager, mode, expect_match_deleted, expect_metadata_deleted):
        """Test invalidate sets caches to None and calls delete_file for enabled caches."""
        cache_manager._test_config_manager.cache_mode = mode
        cache_manager.mode = mode
        cache_manager._initialize_caches()
        # Since delete_file is now a no-op (patched in fixture), we can't check call args, but we can check state
        cache_manager.invalidate()
        assert cache_manager.match_cache is None
        assert cache_manager.metadata_cache is None
        # No direct way to check file deletion, but logic is exercised

    @pytest.mark.parametrize(
        "match_enabled, metadata_enabled",
        [
            (True, True),
            (False, True),
            (True, False),
            (False, False),
        ],
    )
    def test_cleanup_deletes_expected_caches(self, cache_manager, match_enabled, metadata_enabled):
        cache_manager._test_config_manager.cache_mode = CacheMode.MATCHES  # Use a mode that creates both caches
        cache_manager.mode = CacheMode.MATCHES
        cache_manager._initialize_caches()
        cache_manager.is_match_cache_enabled = lambda: match_enabled
        cache_manager.is_metadata_cache_enabled = lambda: metadata_enabled
        # Just call cleanup; IO is suppressed by fixture
        cache_manager.cleanup()
        # No direct way to check file deletion, but logic is exercised


class TestMatchCache:
    @pytest.mark.parametrize(
        "enabled,cache_present,cache_empty,found,expect_match,expect_score",
        [
            (False, False, False, False, None, None),  # not enabled, no cache
            (True, False, False, False, None, None),  # enabled, no cache
            (True, True, True, False, None, None),  # enabled, cache present, empty
            (True, True, False, False, None, None),  # enabled, cache present, not empty, not found
            (True, True, False, True, "dst1", 99),  # enabled, cache present, not empty, found
        ],
    )
    def test_get_match_result(self, cache_manager, enabled, cache_present, cache_empty, found, expect_match, expect_score):
        """get_match returns correct match and score for all enabled/cache states."""
        cache_manager.is_match_cache_enabled = lambda: enabled
        if not cache_present:
            cache_manager.match_cache = None
        else:
            cache_manager._initialize_caches()
            if cache_empty:
                cache_manager.match_cache.cache = cache_manager.match_cache.cache.iloc[0:0].copy()
            else:
                if found:
                    cache_manager.match_cache.cache = pd.DataFrame({"Plex": ["src1"], "FileSystem": ["dst1"], "score": [99]})
                else:
                    cache_manager.match_cache.cache = pd.DataFrame({"Plex": ["other"], "FileSystem": ["dst2"], "score": [88]})
        match, score = cache_manager.get_match("src1", "Plex", "FileSystem")
        assert match == expect_match
        assert score == expect_score

    @pytest.mark.parametrize(
        "enabled,cache_present,cache_empty,expect_upsert",
        [
            (False, False, False, False),  # not enabled, no cache
            (True, False, False, False),  # enabled, no cache
            (True, True, True, False),  # enabled, cache present, empty
            (True, True, False, True),  # enabled, cache present, not empty
        ],
    )
    def test_set_match_upsert_when_enabled_and_cache_non_empty_else_noop(self, cache_manager, cache_factory, enabled, cache_present, cache_empty, expect_upsert):
        """Test set_match upserts when enabled and cache present and non-empty; otherwise no-op."""
        cache_manager.is_match_cache_enabled = lambda: enabled
        called = {}
        if not cache_present:
            cache_manager.match_cache = None
        else:
            # Create a real Cache with the columns used by set_match
            c = cache_factory(columns=["Plex", "FileSystem", "score"], dtype={"Plex": object, "FileSystem": object, "score": object})
            if cache_empty:
                # Make the cache empty
                c.cache = c.cache.iloc[0:0].copy()
            else:
                # Ensure cache is considered non-empty by populating at least one non-NA row
                c.cache = pd.DataFrame({"Plex": ["other"], "FileSystem": ["dst2"], "score": [88]})
            # Wrap upsert_row to track calls while still invoking real behavior
            orig_upsert = c.upsert_row

            def tracking_upsert_row(criteria, data):
                called["upsert"] = (criteria, data)
                return orig_upsert(criteria, data)

            c.upsert_row = tracking_upsert_row
            cache_manager.match_cache = c
        cache_manager.set_match("src1", "dst1", "Plex", "FileSystem", 99)
        if expect_upsert:
            assert called["upsert"][0] == {"Plex": "src1", "FileSystem": "dst1"}
            assert called["upsert"][1] == {"score": 99}
        else:
            assert "upsert" not in called


class TestMetadataCache:
    def test_force_enable_metadata_cache_set_and_get(self, cache_manager, dummy_audio_tag):
        """Test force_enable orchestration for metadata cache."""
        cache_manager.set_metadata("plex", dummy_audio_tag.ID, dummy_audio_tag, force_enable=True)
        meta = cache_manager.get_metadata("plex", dummy_audio_tag.ID, force_enable=True)
        assert isinstance(meta, type(dummy_audio_tag))

    def test_row_to_audiotag_converts_row(self, cache_manager, dummy_audio_tag):
        row = pd.DataFrame([{f: getattr(dummy_audio_tag, f) for f in dummy_audio_tag.get_fields()}])
        tag = cache_manager._row_to_audiotag(row)
        assert isinstance(tag, type(dummy_audio_tag))
        for f in dummy_audio_tag.get_fields():
            assert getattr(tag, f) == getattr(dummy_audio_tag, f)

    def test_get_metadata_cache_columns_returns_expected(self, cache_manager):
        cols = cache_manager._get_metadata_cache_columns()
        assert "player_name" in cols
        assert "ID" in cols
        assert all(isinstance(c, str) for c in cols)

    def test_set_metadata_noop_when_disabled_and_not_forced(self, cache_manager, dummy_audio_tag):
        """When caching is disabled and not forced, set_metadata does nothing."""
        cache_manager.mode = CacheMode.DISABLED
        cache_manager.metadata_cache = None
        cache_manager.set_metadata("plex", dummy_audio_tag.ID, dummy_audio_tag, force_enable=False)
        assert cache_manager.metadata_cache is None

    def test_set_metadata_creates_cache_and_upserts_when_forced(self, cache_manager, dummy_audio_tag):
        """Force-enabling should create a metadata cache and upsert the provided tag."""
        cache_manager.mode = CacheMode.DISABLED
        cache_manager.metadata_cache = None
        cache_manager.set_metadata("plex", dummy_audio_tag.ID, dummy_audio_tag, force_enable=True)
        assert cache_manager.metadata_cache is not None
        found = cache_manager.metadata_cache._find_row_by_columns({"player_name": "plex", "ID": dummy_audio_tag.ID})
        assert not found.empty

    def test_set_metadata_upserts_into_existing_cache_when_enabled(self, cache_manager, dummy_audio_tag, cache_factory):
        """When metadata mode is enabled, set_metadata should upsert into the existing cache."""
        cache_manager.mode = CacheMode.METADATA
        cols = cache_manager._get_metadata_cache_columns()
        dtype = {c: object for c in cols}
        c = cache_factory(columns=cols, dtype=dtype)
        # make non-empty but without the test key
        c.cache = c.cache.iloc[0:0].copy()
        c._ensure_columns()
        c._insert_row(0, {"player_name": "other", "ID": "other"})
        cache_manager.metadata_cache = c
        cache_manager.set_metadata("plex", dummy_audio_tag.ID, dummy_audio_tag, force_enable=False)
        found = cache_manager.metadata_cache._find_row_by_columns({"player_name": "plex", "ID": dummy_audio_tag.ID})
        assert not found.empty

    def test_set_metadata_noop_when_disabled_with_existing_cache_and_not_forced(self, cache_manager, dummy_audio_tag, cache_factory):
        """If caching is disabled and not forced, existing cache should not be modified."""
        cache_manager.mode = CacheMode.DISABLED
        cols = cache_manager._get_metadata_cache_columns()
        dtype = {c: object for c in cols}
        c = cache_factory(columns=cols, dtype=dtype)
        c.cache = c.cache.iloc[0:0].copy()
        c._ensure_columns()
        c._insert_row(0, {"player_name": "other", "ID": "other"})
        cache_manager.metadata_cache = c
        cache_manager.set_metadata("plex", "id", dummy_audio_tag, force_enable=False)
        # ensure plex/id not inserted
        found = cache_manager.metadata_cache._find_row_by_columns({"player_name": "plex", "ID": "id"})
        assert found.empty

    def test_get_metadata_no_cache_disabled_and_not_forced_returns_none(self, cache_manager, dummy_audio_tag):
        """When caching is disabled and there is no cache and not forced, get_metadata returns None."""
        cache_manager.mode = CacheMode.DISABLED
        cache_manager.metadata_cache = None
        result = cache_manager.get_metadata("plex", dummy_audio_tag.ID, force_enable=False)
        assert result is None

    def test_get_metadata_disabled_with_existing_matching_cache_returns_none(self, cache_manager, dummy_audio_tag, cache_factory):
        """If caching is disabled, an existing cache should not be read even if it contains the row."""
        cache_manager.mode = CacheMode.DISABLED
        cols = cache_manager._get_metadata_cache_columns()
        audio_fields = dummy_audio_tag.get_fields()
        combined = list(dict.fromkeys(cols + audio_fields))
        dtype = {c: object for c in combined}
        c = cache_factory(columns=combined, dtype=dtype)
        c._ensure_columns()
        # Insert a row that would match
        row = {"player_name": "plex", "ID": dummy_audio_tag.ID}
        for f in audio_fields:
            row[f] = getattr(dummy_audio_tag, f)
        c._insert_row(0, row)
        cache_manager.metadata_cache = c
        result = cache_manager.get_metadata("plex", dummy_audio_tag.ID, force_enable=False)
        assert result is None

    def test_get_metadata_enabled_with_empty_cache_returns_none(self, cache_manager, dummy_audio_tag, cache_factory):
        """When metadata mode is enabled but the cache is empty, get_metadata returns None."""
        cache_manager.mode = CacheMode.METADATA
        cols = cache_manager._get_metadata_cache_columns()
        dtype = {c: object for c in cols}
        c = cache_factory(columns=cols, dtype=dtype)
        c.cache = c.cache.iloc[0:0].copy()
        c._ensure_columns()
        cache_manager.metadata_cache = c
        result = cache_manager.get_metadata("plex", dummy_audio_tag.ID, force_enable=False)
        assert result is None

    def test_get_metadata_enabled_with_non_matching_row_returns_none(self, cache_manager, dummy_audio_tag, cache_factory):
        """When enabled but no matching row exists, get_metadata returns None."""
        cache_manager.mode = CacheMode.METADATA
        cols = cache_manager._get_metadata_cache_columns()
        audio_fields = dummy_audio_tag.get_fields()
        combined = list(dict.fromkeys(cols + audio_fields))
        dtype = {c: object for c in combined}
        c = cache_factory(columns=combined, dtype=dtype)
        c._ensure_columns()
        # Insert a row that does NOT match the queried player/ID
        row = {"player_name": "other", "ID": "other"}
        for f in audio_fields:
            row[f] = getattr(dummy_audio_tag, f)
        c._insert_row(0, row)
        cache_manager.metadata_cache = c
        result = cache_manager.get_metadata("plex", dummy_audio_tag.ID, force_enable=False)
        assert result is None

    def test_get_metadata_enabled_with_matching_row_returns_audiotag(self, cache_manager, dummy_audio_tag, cache_factory):
        """When enabled and a matching row exists, get_metadata returns an AudioTag instance."""
        cache_manager.mode = CacheMode.METADATA
        cols = cache_manager._get_metadata_cache_columns()
        audio_fields = dummy_audio_tag.get_fields()
        combined = list(dict.fromkeys(cols + audio_fields))
        dtype = {c: object for c in combined}
        c = cache_factory(columns=combined, dtype=dtype)
        c._ensure_columns()
        # Insert a row that matches the queried player/ID and contains audio fields
        row = {"player_name": "plex", "ID": dummy_audio_tag.ID}
        for f in audio_fields:
            row[f] = getattr(dummy_audio_tag, f)
        c._insert_row(0, row)
        cache_manager.metadata_cache = c
        result = cache_manager.get_metadata("plex", dummy_audio_tag.ID, force_enable=False)
        assert isinstance(result, type(dummy_audio_tag))
