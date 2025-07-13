import pytest

from manager.stats_manager import StatsManager, StatusManager


class TestStatsManagerIncrement:
    def test_increment_simple_key_increases_value(self):
        stats = StatsManager()
        assert stats.get("tracks_processed") == 0
        stats.increment("tracks_processed")
        assert stats.get("tracks_processed") == 1
        stats.increment("tracks_processed", 2)
        assert stats.get("tracks_processed") == 3

    @pytest.mark.parametrize("amount", [1, 5, -2])
    def test_increment_with_various_amounts(self, amount):
        stats = StatsManager()
        stats.increment("custom", amount)
        assert stats.get("custom") == amount

    def test_increment_unset_key_initializes_to_amount(self):
        stats = StatsManager()
        stats.increment("unset_key", 7)
        assert stats.get("unset_key") == 7


class TestStatsManagerSet:
    def test_set_simple_key_overwrites_value(self):
        stats = StatsManager()
        stats.increment("foo", 5)
        assert stats.get("foo") == 5
        stats.set("foo", 42)
        assert stats.get("foo") == 42

    def test_set_nested_key_overwrites_value(self):
        stats = StatsManager()
        stats.increment("a::b::c", 3)
        assert stats.get("a::b::c") == 3
        stats.set("a::b::c", 99)
        assert stats.get("a::b::c") == 99

    def test_set_new_nested_key_creates_path(self):
        stats = StatsManager()
        stats.set("x::y::z", 123)
        assert stats.get("x::y::z") == 123


class TestStatsManagerNested:
    def test_increment_and_get_nested_keys(self):
        stats = StatsManager()
        stats.increment("A::B::C")
        stats.increment("A::B::C")
        stats.increment("A::B::D")
        assert stats.get("A::B::C") == 2
        assert stats.get("A::B::D") == 1
        assert stats.get("A::B::E") == 0

    def test_set_and_get_deeply_nested_key(self):
        stats = StatsManager()
        stats.set("foo::bar::baz::qux", 55)
        assert stats.get("foo::bar::baz::qux") == 55

    def test_get_unset_key_returns_zero(self):
        stats = StatsManager()
        assert stats.get("nonexistent") == 0
        assert stats.get("a::b::c::d") == 0


class TestStatusManagerLifecycle:
    def test_start_and_close_phase_removes_bar(self):
        sm = StatusManager()
        bar = sm.start_phase("Phase1", total=5)
        assert "Phase1" in sm.bars
        bar.update(1)
        bar.close()
        assert "Phase1" not in sm.bars

    def test_starting_multiple_phases_creates_multiple_bars(self):
        sm = StatusManager()
        bar1 = sm.start_phase("PhaseA", total=2)
        bar2 = sm.start_phase("PhaseB", total=3)
        assert "PhaseA" in sm.bars
        assert "PhaseB" in sm.bars
        bar1.close()
        assert "PhaseA" not in sm.bars
        assert "PhaseB" in sm.bars
        bar2.close()
        assert "PhaseB" not in sm.bars

    def test_restarting_phase_replaces_bar(self):
        sm = StatusManager()
        bar1 = sm.start_phase("Repeat", total=1)
        bar1_id = id(bar1)
        bar2 = sm.start_phase("Repeat", total=2)
        assert id(bar2) != bar1_id
        assert "Repeat" in sm.bars
        bar2.close()
        assert "Repeat" not in sm.bars


class TestStatusManagerBarBehavior:
    def test_managed_progress_bar_bool_is_true(self):
        sm = StatusManager()
        bar = sm.start_phase("BoolTest", total=1)
        assert bool(bar) is True
        bar.close()

    def test_managed_progress_bar_close_removes_from_bars(self):
        sm = StatusManager()
        bar = sm.start_phase("CloseTest", total=1)
        assert "CloseTest" in sm.bars
        bar.close()
        assert "CloseTest" not in sm.bars
