"""Trade Manager testleri — seviye matematiği, ilerleme, çıkış."""

import math
import pytest

from core.trade_manager import (
    Trade, TradeLevels, compute_levels, update_level, check_exit,
    unrealized_pnl_usdt, unrealized_pnl_pct, LEVEL_NAMES,
)


# ============================================================================
# LEVEL HESAPLAMA
# ============================================================================

class TestComputeLevelsLong:
    def test_donchian_within_2pct(self):
        """Donchian alt entry'ye %1 uzaklıkta → LOSE_EXIT = donchian alt."""
        entry = 100.0
        donchian_low = 99.0  # %1 uzaklık
        L = compute_levels("long", entry, donchian_low, sl_percent=2.0, rr=3.0)
        assert L.side == "long"
        assert math.isclose(L.lose_exit, 99.0)
        # d = 1.0, winrate = 100 + 3 = 103
        assert math.isclose(L.winrate, 103.0)
        # step = (103-100)/6 = 0.5
        # step_lines: ST1L=100.5, ST2L=101.0, ST3L=101.5, ST4L=102.0, ST5L=102.5, WR=103.0
        expected = [100.5, 101.0, 101.5, 102.0, 102.5, 103.0]
        for got, exp in zip(L.step_lines, expected):
            assert math.isclose(got, exp), f"got {got}, expected {exp}"

    def test_donchian_beyond_2pct_clamps(self):
        """Donchian alt entry'den %5 uzakta → LOSE_EXIT %2'ye çekilir."""
        entry = 100.0
        donchian_low = 95.0  # %5 uzaklık → clamp olmalı
        L = compute_levels("long", entry, donchian_low, sl_percent=2.0, rr=3.0)
        assert math.isclose(L.lose_exit, 98.0)  # entry * 0.98
        # d = 2, winrate = 106
        assert math.isclose(L.winrate, 106.0)
        # step = 1.0 → ST1L=101, ST2L=102, ..., WR=106
        expected = [101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
        for got, exp in zip(L.step_lines, expected):
            assert math.isclose(got, exp)

    def test_donchian_above_entry_anomaly(self):
        """Anomal: donchian alt entry'nin üzerinde — güvenli davranış %2 SL."""
        entry = 100.0
        donchian_low = 101.0
        L = compute_levels("long", entry, donchian_low, sl_percent=2.0, rr=3.0)
        assert math.isclose(L.lose_exit, 98.0)

    def test_step_lines_monotonic_increasing(self):
        L = compute_levels("long", 50000.0, 49000.0, sl_percent=2.0, rr=3.0)
        for i in range(1, len(L.step_lines)):
            assert L.step_lines[i] > L.step_lines[i - 1]
        assert L.step_lines[0] > L.entry
        assert L.step_lines[-1] == pytest.approx(L.winrate)


class TestComputeLevelsShort:
    def test_donchian_within_2pct(self):
        entry = 100.0
        donchian_high = 101.0  # %1 yukarı
        L = compute_levels("short", entry, donchian_high, sl_percent=2.0, rr=3.0)
        assert math.isclose(L.lose_exit, 101.0)
        # d = 1, winrate = 100 - 3 = 97
        assert math.isclose(L.winrate, 97.0)
        # step = 0.5; ST1L=99.5, ST2L=99.0, ..., WR=97.0
        expected = [99.5, 99.0, 98.5, 98.0, 97.5, 97.0]
        for got, exp in zip(L.step_lines, expected):
            assert math.isclose(got, exp)

    def test_donchian_beyond_2pct_clamps(self):
        entry = 100.0
        donchian_high = 105.0
        L = compute_levels("short", entry, donchian_high, sl_percent=2.0, rr=3.0)
        assert math.isclose(L.lose_exit, 102.0)
        assert math.isclose(L.winrate, 94.0)

    def test_step_lines_monotonic_decreasing(self):
        L = compute_levels("short", 50000.0, 51000.0, sl_percent=2.0, rr=3.0)
        for i in range(1, len(L.step_lines)):
            assert L.step_lines[i] < L.step_lines[i - 1]
        assert L.step_lines[0] < L.entry
        assert L.step_lines[-1] == pytest.approx(L.winrate)


# ============================================================================
# LEVEL TAKİBİ
# ============================================================================

def _make_long_trade():
    levels = compute_levels("long", 100.0, 99.0)  # d=1, winrate=103
    return Trade(symbol="BTCUSDT", side="long", size=1.0, levels=levels,
                 notional_usdt=100.0, stake_usdt=2.0)


def _make_short_trade():
    levels = compute_levels("short", 100.0, 101.0)  # d=1, winrate=97
    return Trade(symbol="BTCUSDT", side="short", size=1.0, levels=levels,
                 notional_usdt=100.0, stake_usdt=2.0)


class TestUpdateLevelLong:
    def test_stays_at_entry_when_price_below_st1(self):
        t = _make_long_trade()
        assert t.level == 1
        result = update_level(t, 100.4)
        assert result is None
        assert t.level == 1

    def test_advances_to_st1(self):
        t = _make_long_trade()
        # ST1 line = 100.5
        result = update_level(t, 100.5)
        assert result == 2
        assert t.level == 2

    def test_advances_to_st5_in_one_jump(self):
        """Tek tick'te birden fazla seviye atlanabilir (gap fiyat)."""
        t = _make_long_trade()
        # ST5 line = 102.5, fiyat oraya zıpladı
        result = update_level(t, 102.5)
        assert result == 6
        assert t.level == 6

    def test_does_not_advance_past_6(self):
        t = _make_long_trade()
        t.level = 6
        # WINRATE = 103, fiyat üstüne çıksa bile level=6 kalır (WINRATE exit'i tetikler)
        result = update_level(t, 105.0)
        assert result is None
        assert t.level == 6

    def test_level_does_not_go_down(self):
        """Spec: seviye yalnızca yukarı sayılır, geri düşmez."""
        t = _make_long_trade()
        update_level(t, 101.5)  # ST3'e çıktı → level=4
        assert t.level == 4
        result = update_level(t, 100.1)  # geri düştü
        assert result is None
        assert t.level == 4  # değişmedi


class TestUpdateLevelShort:
    def test_advances_to_st1(self):
        t = _make_short_trade()
        # ST1 line = 99.5
        result = update_level(t, 99.5)
        assert result == 2

    def test_advances_to_st5_in_one_jump(self):
        t = _make_short_trade()
        # ST5 line = 97.5
        update_level(t, 97.5)
        assert t.level == 6

    def test_level_does_not_go_down(self):
        t = _make_short_trade()
        update_level(t, 98.5)  # ST3 line; level=4
        assert t.level == 4
        update_level(t, 99.9)
        assert t.level == 4


# ============================================================================
# ÇIKIŞ KONTROLÜ
# ============================================================================

class TestCheckExitLong:
    def test_level1_exits_at_lose_exit(self):
        t = _make_long_trade()  # lose_exit=99
        assert check_exit(t, price=99.0, ema_val=None) == "lose_exit"
        assert check_exit(t, price=98.5, ema_val=None) == "lose_exit"

    def test_level1_no_exit_above_lose_exit(self):
        t = _make_long_trade()
        assert check_exit(t, price=99.5, ema_val=None) is None
        assert check_exit(t, price=100.4, ema_val=None) is None

    def test_level2_still_uses_lose_exit(self):
        t = _make_long_trade()
        t.level = 2
        assert check_exit(t, price=99.0, ema_val=None) == "lose_exit"
        # entry'ye değdi ama level=2 → çıkış YOK
        assert check_exit(t, price=100.0, ema_val=None) is None

    def test_level3_uses_entry_line(self):
        t = _make_long_trade()
        t.level = 3
        # entry = 100
        assert check_exit(t, price=100.0, ema_val=None) == "trail_back"
        assert check_exit(t, price=99.99, ema_val=None) == "trail_back"
        assert check_exit(t, price=100.01, ema_val=None) is None

    def test_level4_uses_st1_line(self):
        t = _make_long_trade()
        t.level = 4
        # ST1 line = 100.5
        assert check_exit(t, price=100.5, ema_val=None) == "trail_back"
        assert check_exit(t, price=100.51, ema_val=None) is None

    def test_level5_uses_st2_line(self):
        t = _make_long_trade()
        t.level = 5
        # ST2 line = 101.0
        assert check_exit(t, price=101.0, ema_val=None) == "trail_back"

    def test_level6_uses_st3_line_and_winrate(self):
        t = _make_long_trade()
        t.level = 6
        # ST3 line = 101.5, WR=103
        assert check_exit(t, price=101.5, ema_val=None) == "trail_back"
        assert check_exit(t, price=103.0, ema_val=None) == "winrate"
        assert check_exit(t, price=103.5, ema_val=None) == "winrate"
        assert check_exit(t, price=102.0, ema_val=None) is None

    def test_ema_hit_with_prev_price(self):
        t = _make_long_trade()
        # prev=100.3, cur=99.9, ema=100.1 → cross
        assert check_exit(t, price=99.9, ema_val=100.1, prev_price=100.3) == "ema_hit"

    def test_ema_no_hit_when_both_above(self):
        t = _make_long_trade()
        assert check_exit(t, price=100.4, ema_val=99.0, prev_price=100.3) is None


class TestCheckExitShort:
    def test_level1_exits_at_lose_exit(self):
        t = _make_short_trade()  # lose_exit=101
        assert check_exit(t, price=101.0, ema_val=None) == "lose_exit"
        assert check_exit(t, price=101.5, ema_val=None) == "lose_exit"

    def test_level1_no_exit_below_lose_exit(self):
        t = _make_short_trade()
        assert check_exit(t, price=100.5, ema_val=None) is None

    def test_level3_uses_entry_line(self):
        t = _make_short_trade()
        t.level = 3
        assert check_exit(t, price=100.0, ema_val=None) == "trail_back"
        assert check_exit(t, price=99.99, ema_val=None) is None

    def test_level6_uses_st3_line_and_winrate(self):
        t = _make_short_trade()
        t.level = 6
        # ST3 line = 98.5, WR = 97
        assert check_exit(t, price=98.5, ema_val=None) == "trail_back"
        assert check_exit(t, price=97.0, ema_val=None) == "winrate"
        assert check_exit(t, price=98.0, ema_val=None) is None


# ============================================================================
# E2E SIMÜLASYON
# ============================================================================

class TestEndToEndLong:
    def test_winrate_journey(self):
        """Fiyat: entry → ST5 boyunca yukarı → winrate."""
        t = _make_long_trade()
        # Aşamalı yukarı
        for price, expected_level in [
            (100.5, 2),  # ST1
            (101.0, 3),  # ST2
            (101.5, 4),  # ST3
            (102.0, 5),  # ST4
            (102.5, 6),  # ST5
        ]:
            update_level(t, price)
            assert t.level == expected_level, f"price {price}, level {t.level}"
            assert check_exit(t, price, ema_val=None) is None
        # Winrate
        assert check_exit(t, 103.0, ema_val=None) == "winrate"

    def test_trail_back_after_climb(self):
        """Fiyat ST3'e çıktı, sonra ST1 line altına düştü → trail_back."""
        t = _make_long_trade()
        update_level(t, 101.5)  # ST3 zone → level 4
        assert t.level == 4
        # ST1 line = 100.5 — bu çizgi altı tetikler
        assert check_exit(t, 100.5, ema_val=None) == "trail_back"

    def test_stop_loss_path(self):
        """Fiyat hiç yükselmedi, lose_exit'e düştü."""
        t = _make_long_trade()
        update_level(t, 100.2)
        assert t.level == 1
        assert check_exit(t, 99.0, ema_val=None) == "lose_exit"


# ============================================================================
# PnL
# ============================================================================

class TestPnL:
    def test_long_profit(self):
        t = _make_long_trade()
        # entry=100, price=102, size=1 → 2 USDT profit
        assert math.isclose(unrealized_pnl_usdt(t, 102.0), 2.0)

    def test_long_loss(self):
        t = _make_long_trade()
        assert math.isclose(unrealized_pnl_usdt(t, 98.0), -2.0)

    def test_short_profit(self):
        t = _make_short_trade()
        # entry=100, price=98 → 2 USDT profit
        assert math.isclose(unrealized_pnl_usdt(t, 98.0), 2.0)

    def test_pct_uses_stake(self):
        t = _make_long_trade()  # stake = 2.0
        # 2 USDT profit / 2 USDT stake = 100%
        assert math.isclose(unrealized_pnl_pct(t, 102.0), 100.0)
