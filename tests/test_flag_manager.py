"""Flag Manager testleri."""

import pandas as pd
import pytest

from core.flag_manager import (
    FlagManager, detect_flag_signal, price_crossed_ema,
)


def _df(rows):
    """Yardımcı: [(ts, open, high, low, close, ema, du, dl), ...] → DataFrame."""
    return pd.DataFrame(rows, columns=[
        "timestamp", "open", "high", "low", "close",
        "ema", "donchian_upper", "donchian_lower",
    ])


class TestDetectFlagSignal:
    def test_long_flag_donchian_upper_drops(self):
        # close > ema (uptrend), donchian üst düştü
        df = _df([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),  # üst 110→108 düştü, close>ema
        ])
        assert detect_flag_signal(df) == "long"

    def test_short_flag_donchian_lower_rises(self):
        # close < ema (downtrend), donchian alt yükseldi
        df = _df([
            (1, 100, 105, 99, 98, 105, 110, 90),
            (2, 98, 99, 92, 95, 104, 110, 92),  # alt 90→92 yükseldi, close<ema
        ])
        assert detect_flag_signal(df) == "short"

    def test_no_flag_when_donchian_stable(self):
        df = _df([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 110, 95),  # değişim yok
        ])
        assert detect_flag_signal(df) is None

    def test_long_flag_blocked_by_trend_filter(self):
        """Donchian üst düştü ama fiyat EMA altında → long flag YOK."""
        df = _df([
            (1, 100, 105, 99, 102, 110, 110, 95),
            (2, 102, 106, 100, 100, 110, 108, 95),  # üst düştü ama close<ema
        ])
        assert detect_flag_signal(df) is None

    def test_short_flag_blocked_by_trend_filter(self):
        df = _df([
            (1, 100, 105, 99, 98, 90, 110, 90),
            (2, 98, 99, 92, 95, 90, 110, 92),  # alt yükseldi ama close>ema
        ])
        assert detect_flag_signal(df) is None

    def test_insufficient_data(self):
        df = _df([(1, 100, 105, 99, 102, 95, 110, 95)])
        assert detect_flag_signal(df) is None

    def test_nan_in_indicators(self):
        df = _df([
            (1, 100, 105, 99, 102, float("nan"), 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),
        ])
        # prev'in ema'si NaN — biz son satıra bakıyoruz; prev'in ema'sı problemli değil
        # ama prev'in donchian'ları da gerekli. Burada hepsi non-NaN olmalı.
        # Bu testte sinyal long olmalı:
        assert detect_flag_signal(df) == "long"


class TestPriceCrossedEMA:
    def test_cross_upward(self):
        # prev altta, cur üstte
        assert price_crossed_ema(99.0, 101.0, 100.0) is True

    def test_cross_downward(self):
        assert price_crossed_ema(101.0, 99.0, 100.0) is True

    def test_touch_exact(self):
        assert price_crossed_ema(101.0, 100.0, 100.0) is True

    def test_no_cross_both_above(self):
        assert price_crossed_ema(101.0, 102.0, 100.0) is False

    def test_no_cross_both_below(self):
        assert price_crossed_ema(99.0, 98.0, 100.0) is False


class TestFlagManager:
    def test_creates_long_flag_on_candle_close(self):
        df = _df([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),
        ])
        fm = FlagManager()
        flag = fm.on_candle_close("BTCUSDT", df)
        assert flag is not None
        assert flag.side == "long"
        assert flag.entry_trigger_price == 108
        assert fm.get_flag("BTCUSDT", "long") is not None
        assert fm.get_flag("BTCUSDT", "short") is None

    def test_creates_short_flag_on_candle_close(self):
        df = _df([
            (1, 100, 105, 99, 98, 105, 110, 90),
            (2, 98, 99, 92, 95, 104, 110, 92),
        ])
        fm = FlagManager()
        flag = fm.on_candle_close("ETHUSDT", df)
        assert flag is not None
        assert flag.side == "short"
        assert flag.entry_trigger_price == 92

    def test_check_entry_trigger_long(self):
        df = _df([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),
        ])
        fm = FlagManager()
        fm.on_candle_close("BTCUSDT", df)
        # Long trigger = 108
        assert fm.check_entry_trigger("BTCUSDT", 107.99) is None
        assert fm.check_entry_trigger("BTCUSDT", 108.0) == "long"
        assert fm.check_entry_trigger("BTCUSDT", 110.0) == "long"

    def test_check_entry_trigger_short(self):
        df = _df([
            (1, 100, 105, 99, 98, 105, 110, 90),
            (2, 98, 99, 92, 95, 104, 110, 92),
        ])
        fm = FlagManager()
        fm.on_candle_close("ETHUSDT", df)
        # Short trigger = 92
        assert fm.check_entry_trigger("ETHUSDT", 92.01) is None
        assert fm.check_entry_trigger("ETHUSDT", 92.0) == "short"
        assert fm.check_entry_trigger("ETHUSDT", 90.0) == "short"

    def test_consume_flag(self):
        df = _df([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),
        ])
        fm = FlagManager()
        fm.on_candle_close("BTCUSDT", df)
        assert fm.get_flag("BTCUSDT", "long") is not None
        consumed = fm.consume_flag("BTCUSDT", "long")
        assert consumed is not None
        assert fm.get_flag("BTCUSDT", "long") is None

    def test_new_flag_overwrites_old(self):
        """İşlem açıkken yeni flag oluştuysa eskinin üzerine yazar."""
        df1 = _df([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),
        ])
        df2 = _df([
            (2, 102, 106, 100, 104, 96, 108, 96),
            (3, 104, 107, 102, 106, 97, 107, 97),  # üst tekrar düştü 108→107
        ])
        fm = FlagManager()
        fm.on_candle_close("BTCUSDT", df1)
        first = fm.get_flag("BTCUSDT", "long")
        fm.on_candle_close("BTCUSDT", df2)
        second = fm.get_flag("BTCUSDT", "long")
        assert second is not None
        assert second.entry_trigger_price == 107  # yenilendi
        assert first.entry_trigger_price == 108   # eski hâlâ referansta

    def test_price_cross_ema_clears_flag(self):
        df = _df([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),
        ])
        fm = FlagManager()
        fm.on_candle_close("BTCUSDT", df)
        assert fm.get_flag("BTCUSDT", "long") is not None
        # prev=104, cur=95, ema=96 → cross down
        removed = fm.on_price_cross_ema("BTCUSDT", 104, 95, 96)
        assert "long" in removed
        assert fm.get_flag("BTCUSDT", "long") is None

    def test_dump_load_roundtrip(self):
        df = _df([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),
        ])
        fm1 = FlagManager()
        fm1.on_candle_close("BTCUSDT", df)
        data = fm1.dump()

        fm2 = FlagManager(state=data)
        flag = fm2.get_flag("BTCUSDT", "long")
        assert flag is not None
        assert flag.entry_trigger_price == 108
