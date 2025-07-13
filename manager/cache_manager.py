import logging
import os
import pickle
import warnings
from datetime import datetime
from typing import List

import pandas as pd

from manager import get_manager
from MediaPlayer import FileSystem, MediaMonkey, Plex
from sync_items import AudioTag

warnings.simplefilter(action="ignore", category=pd.errors.PerformanceWarning)


class Cache:
    """Generic Cache class to handle common operations for caching."""

    def __init__(self, filepath: str, columns: list, dtype: dict, save_threshold: int = 100, max_age_hours: int = 720) -> None:
        self.filepath = filepath
        self.max_age_hours = max_age_hours
        self.columns = columns
        self.dtype = dtype
        self.save_threshold = save_threshold
        self.logger = logging.getLogger("PlexSync.Cache")
        self.cache: pd.DataFrame = self._initialize_cache()
        self.update_count = 0

    def _initialize_cache(self) -> pd.DataFrame:
        """Initialize a new cache with the required columns."""
        self.logger.debug(f"Initializing cache with columns: {self.columns}")
        df = pd.DataFrame(data=None, index=range(self.save_threshold + 1), columns=self.columns).astype(self.dtype)
        self.logger.info(f"Cache initialized with {self.save_threshold + 1} empty rows")
        return df

    def load(self) -> None:
        """Load the cache from the file."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "rb") as f:
                    self.cache = pickle.load(f)
                self.logger.info(f"Cache loaded from {self.filepath}: {len(self.cache)} entries")
                self._ensure_columns()
            except (pickle.UnpicklingError, EOFError) as e:
                self.logger.error(f"Failed to load cache from {self.filepath}: {e}")
                self.logger.warning("Falling back to new cache initialization")
                self.cache = self._initialize_cache()

            if self.max_age_hours is not None:
                file_mtime = os.path.getmtime(self.filepath)
                now = datetime.now().timestamp()
                age_seconds = now - file_mtime
                age_hours = age_seconds / (60 * 60)

                if age_hours > self.max_age_hours:
                    self.logger.warning(f"Cache file {self.filepath} is {age_hours:.1f} hours old — exceeding max age of {self.max_age_hours} hours.")
                    self.logger.warning("Discarding cache and reinitializing.")
                    self.delete()
                    self.cache = self._initialize_cache()
                    return

        else:
            self.logger.debug(f"No existing cache found at {self.filepath}. Initializing new cache.")
            self.cache = self._initialize_cache()

    def save(self) -> None:
        """Save the cache to the file."""
        try:
            with open(self.filepath, "wb") as f:
                pickle.dump(self.cache, f)
            self.logger.info(f"Cache saved to {self.filepath}: {len(self.cache)} entries")
        except Exception as e:
            self.logger.error(f"Failed to save cache to {self.filepath}: {e}")

    @staticmethod
    def delete_file(filepath: str) -> None:
        """Delete the cache file at the given path, logging the result."""
        if os.path.exists(filepath):
            logger = logging.getLogger("PlexSync.Cache")
            try:
                os.remove(filepath)
                logger.info(f"Cache file {filepath} deleted successfully")
            except Exception as e:
                logger.error(f"Failed to delete cache file {filepath}: {e}")

    def delete(self) -> None:
        """Delete the cache file."""
        self.delete_file(self.filepath)

    def _ensure_columns(self) -> None:
        """Ensure all required columns are present in the cache."""
        for column in self.columns:
            if column not in self.cache.columns:
                self.logger.warning(f"Missing column '{column}' in cache. Adding it with NaN values.")
                self.cache[column] = pd.NA

    def resize(self, additional_rows: int = 100) -> None:
        """Resize the cache by adding more empty rows."""
        start_index = self.cache.index.max() + 1 if not self.is_empty() else 0
        new_index = range(start_index, start_index + additional_rows)
        self.cache = self.cache.reindex(self.cache.index.union(new_index))
        self.logger.debug(f"Cache resized by {additional_rows} rows. New size: {len(self.cache)}")

    def auto_save(self) -> None:
        """Trigger an auto-save if the update count exceeds the threshold."""
        if self.update_count >= self.save_threshold:
            self.cache = self.cache.dropna(how="all").copy()
            self.save()
            self.update_count = 0

    def is_empty(self) -> bool:
        """Check if the cache DataFrame is empty or all rows are all-NA. Always returns a native Python bool."""
        result = self.cache.empty or self.cache.isna().all(axis=1).all()
        return bool(result)

    def _find_row_by_columns(self, criteria: dict) -> pd.DataFrame:
        """Return DataFrame rows matching all criteria (column: value)."""
        mask = pd.Series([True] * len(self.cache))
        for col, val in criteria.items():
            mask &= self.cache[col] == val
        return self.cache[mask]

    def _find_empty_row_or_resize(self) -> int:
        """Return index of first all-NaN row, resizing if needed."""
        empty_rows = self.cache.index[self.cache.isna().all(axis=1)]
        if not empty_rows.empty:
            return empty_rows[0]
        self.resize()
        empty_rows = self.cache.index[self.cache.isna().all(axis=1)]
        return empty_rows[0]

    def _update_row(self, row_index: int, data: dict) -> None:
        """Update row at row_index with data dict."""
        for key, value in data.items():
            if key in self.cache.columns:
                self.cache.loc[row_index, key] = value

    def _insert_row(self, row_index: int, data: dict) -> None:
        """Insert data dict into row at row_index, handling None as pd.NA."""
        for key, value in data.items():
            if key in self.cache.columns:
                self.cache.loc[row_index, key] = pd.NA if value is None else value

    def upsert_row(self, criteria: dict, data: dict) -> None:
        """Update row matching criteria or insert new row with data."""
        existing_row = self._find_row_by_columns(criteria)
        if not existing_row.empty:
            row_index = existing_row.index[0]
            self._update_row(row_index, data)
        else:
            empty_row_idx = self._find_empty_row_or_resize()
            self._insert_row(empty_row_idx, {**criteria, **data})
        self.update_count += 1
        self.auto_save()


class CacheManager:
    """Handles caching for metadata and track matches, supporting multiple caching modes."""

    KNOWN_PLAYERS = [MediaMonkey, Plex, FileSystem]
    MATCH_CACHE_FILE = "matches_cache.pkl"
    METADATA_CACHE_FILE = "metadata_cache.pkl"
    SAVE_THRESHOLD = 100

    def __init__(self) -> None:
        mgr = get_manager()
        self.config_mgr = mgr.get_config_manager()
        self.stats_mgr = mgr.get_stats_manager()
        self.logger = logging.getLogger("PlexSync.CacheManager")
        self.mode = self.config_mgr.cache_mode
        self.metadata_cache = None
        self.match_cache = None

        if self.mode == "disabled":
            self.logger.info("Cache disabled.")
            return

        self._initialize_caches()

    def is_match_cache_enabled(self) -> bool:
        """Return True if match caching is enabled based on current mode."""
        return self.mode in {"matches", "matches-only"}

    def is_metadata_cache_enabled(self) -> bool:
        """Return True if metadata caching is enabled based on current mode."""
        return self.mode in {"metadata", "matches"}

    def _initialize_caches(self) -> None:
        """Initialize both caches based on current mode."""
        self.logger.debug(f"Initializing caches for mode: {self.mode}")
        if self.is_match_cache_enabled():
            self.logger.debug("Initializing match cache")
            self.match_cache = Cache(filepath=self.MATCH_CACHE_FILE, columns=self._get_match_cache_columns(), dtype="object", save_threshold=self.SAVE_THRESHOLD)
            self.match_cache.load()

        if self.is_metadata_cache_enabled():
            self.logger.debug("Initializing metadata cache")
            self.metadata_cache = Cache(
                filepath=self.METADATA_CACHE_FILE, columns=self._get_metadata_cache_columns(), dtype=object, save_threshold=self.SAVE_THRESHOLD, max_age_hours=24
            )
            self.metadata_cache.load()

    def _safe_get_value(self, row: pd.DataFrame, column_name: str) -> object | None:
        """Safely extract a value from a pandas row, converting NaN to None."""
        value = row[column_name].iloc[0]
        return None if pd.isna(value) else value

    def _log_cache_hit(self, cache_type: str, key_info: str) -> None:
        self.logger.trace(f"{cache_type} cache hit for {key_info}")
        self.stats_mgr.increment("cache_hits")

    def _is_cache_ready(self, cache: "Cache", enabled_check: bool) -> bool:
        return enabled_check and cache is not None and not cache.is_empty()

    def _row_to_audiotag(self, row: pd.DataFrame) -> AudioTag:
        data = {key: self._safe_get_value(row, key) for key in row.columns}
        return AudioTag.from_dict(data)

    def _get_match_cache_columns(self) -> list:
        """Get the required columns for the match cache."""
        return [player.name() for player in self.KNOWN_PLAYERS] + ["score"]

    def _get_metadata_cache_columns(self) -> list:
        """Get the required columns for the metadata cache."""
        return list(dict.fromkeys(["player_name", *AudioTag.get_fields()]))

    def cleanup(self) -> None:
        """Clean up cache files from disk."""
        if self.metadata_cache:
            self.metadata_cache.delete()

        if self.match_cache and not self.is_match_cache_enabled():
            self.match_cache.delete()

    def invalidate(self) -> None:
        """Invalidate both match and metadata caches, deleting files even if cache objects are None."""

        if self.match_cache:
            self.match_cache.delete()
        else:
            Cache.delete_file(self.MATCH_CACHE_FILE)
        self.match_cache = None

        if self.metadata_cache:
            self.metadata_cache.delete()
        else:
            Cache.delete_file(self.METADATA_CACHE_FILE)
        self.metadata_cache = None

    ### MATCH CACHING (PERSISTENT) ###
    def get_match(self, source_id: str, source_name: str, dest_name: str) -> str | None:
        """Retrieve a cached match for a track."""
        if not self._is_cache_ready(self.match_cache, self.is_match_cache_enabled()):
            return None, None

        row = self.match_cache._find_row_by_columns({source_name: source_id})
        if row.empty:
            return None, None

        match = self._safe_get_value(row, dest_name)
        score = self._safe_get_value(row, "score")
        self._log_cache_hit("match", f"{source_name}:{source_id} and {dest_name}:{match}")
        return match, score

    def set_match(self, source_id: str, dest_id: str, source_name: str, dest_name: str, score: float | None = None) -> None:
        """Store or update a track match between source and destination, including a score."""
        if not self._is_cache_ready(self.match_cache, self.is_match_cache_enabled()):
            return

        criteria = {source_name: source_id, dest_name: dest_id}
        data = {"score": score}
        self.match_cache.upsert_row(criteria, data)

    def get_metadata(self, player_name: str, track_id: str, force_enable: bool = False) -> AudioTag | None:
        """Retrieve cached metadata by player name and track ID."""
        if not (force_enable or self.is_metadata_cache_enabled()) or self.metadata_cache is None or self.metadata_cache.is_empty():
            return None

        player_name = player_name.strip().lower()
        track_id = str(track_id).strip().lower()

        row = self.metadata_cache._find_row_by_columns({"player_name": player_name, "ID": track_id})
        if row.empty:
            return None

        audiotag = self._row_to_audiotag(row)
        self._log_cache_hit("metadata", f"{player_name}:{track_id}")
        return audiotag

    def set_metadata(self, player_name: str, track_id: str, metadata: AudioTag, force_enable: bool = False) -> None:
        """Store metadata in the pre-allocated cache, resizing if needed."""
        if not (force_enable or self.is_metadata_cache_enabled()):
            return

        player_name = player_name.strip().lower()
        track_id = str(track_id).strip().lower()

        if self.metadata_cache is None or self.metadata_cache.is_empty():
            self.metadata_cache = Cache(
                filepath=self.METADATA_CACHE_FILE,
                columns=self._get_metadata_cache_columns(),
                dtype={col: "str" if col == "ID" else "object" for col in self._get_metadata_cache_columns()},
                save_threshold=self.SAVE_THRESHOLD,
            )
            self.metadata_cache.load()

        criteria = {"player_name": player_name, "ID": str(track_id)}
        metadata_dict = metadata.to_dict()
        metadata_dict["ID"] = str(metadata_dict["ID"]) if metadata_dict["ID"] is not None else None
        self.metadata_cache.upsert_row(criteria, metadata_dict)

    def get_tracks_by_filter(self, filter_mask: pd.Series) -> List[AudioTag]:
        """Convert filtered DataFrame rows into AudioTag objects"""
        matching_rows = self.metadata_cache.cache[filter_mask]
        if matching_rows.empty:
            return []
        return [self._row_to_audiotag(matching_rows.loc[[idx]]) for idx in matching_rows.index]
