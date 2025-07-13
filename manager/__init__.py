from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache_manager import CacheManager
    from .config_manager import ConfigManager
    from .stats_manager import StatsManager, StatusManager


class Manager:
    _instance = None
    _initialized = None

    def __new__(self) -> "Manager":
        if self._instance is None:
            self._instance = super(Manager, self).__new__(self)
        return self._instance

    def initialize(self) -> None:
        from .cache_manager import CacheManager
        from .config_manager import ConfigManager, LogLevel
        from .log_manager import LogManager
        from .stats_manager import StatsManager, StatusManager

        if self._initialized:
            return

        self._log = LogManager()
        self._logger = self._log.setup_logging(LogLevel.WARNING)

        self._config = ConfigManager()
        self._log.update_log_level(self._config.log)

        self._stats = StatsManager()
        self._status = StatusManager()
        self._cache = CacheManager()
        self._initialized = True

    def get_stats_manager(self) -> "StatsManager":
        return self._stats

    def get_status_manager(self) -> "StatusManager":
        return self._status

    def get_cache_manager(self) -> "CacheManager":
        return self._cache

    def get_config_manager(self) -> "ConfigManager":
        return self._config


def get_manager() -> Manager:
    return Manager()
