"""İndikatör testleri."""

import math
import numpy as np
import pandas as pd
import pytest

from core.indicators import ema, donchian, atr, klines_to_df, compute_all


class TestEMA:
    def test_constant_series_returns_constant(self):
        s = pd.Series([10.0] * 100)
        result = ema(s, period=20)
        assert all(abs(v - 10.0) < 1e-9 for v in result)

    def test_known_values(self):
        # alpha = 2/(period+1); period=3 → alpha=0.5
        # EMA[0] = 1.0 (ewm adjust=False: seed = ilk değer)
        # EMA[1] = 0.5*2 + 0.5*1 = 1.5
        # EMA[2] = 0.5*3 + 0.5*1.5 = 2.25
        s = pd.Series([1.0, 2.0, 3.0])
        result = ema(s, period=3)
        assert math.isclose(result.iloc[0], 1.0)
        assert math.isclose(result.iloc[1], 1.5)
        assert math.isclose(result.iloc[2], 2.25)

    def test_length_preserved(self):
        s = pd.Series(np.random.rand(500))
        result = ema(s, period=50)
        assert len(result) == 500


class TestDonchian:
    def test_basic(self):
        h = pd.Series([10, 11, 12, 9, 8, 13])
        l = pd.Series([5, 6, 7, 4, 3, 8])
        u, lo = donchian(h, l, period=3)
        # ilk 2 NaN olmalı
        assert pd.isna(u.iloc[0]) and pd.isna(u.iloc[1])
        # index 2: max(10,11,12)=12, min(5,6,7)=5
        assert u.iloc[2] == 12 and lo.iloc[2] == 5
        # index 3: max(11,12,9)=12, min(6,7,4)=4
        assert u.iloc[3] == 12 and lo.iloc[3] == 4
        # index 5: max(9,8,13)=13, min(4,3,8)=3
        assert u.iloc[5] == 13 and lo.iloc[5] == 3

    def test_period_50_realistic(self):
        np.random.seed(0)
        h = pd.Series(100 + np.random.rand(100) * 10)
        l = pd.Series(95 + np.random.rand(100) * 5)
        u, lo = donchian(h, l, period=50)
        # son değerler son 50 mum üzerinden
        assert u.iloc[-1] == max(h.iloc[-50:])
        assert lo.iloc[-1] == min(l.iloc[-50:])


class TestATR:
    def test_constant_returns_zero_after_warmup(self):
        # Sabit yüksek/düşük/close → TR=0 → ATR=0
        h = pd.Series([10.0] * 50)
        l = pd.Series([10.0] * 50)
        c = pd.Series([10.0] * 50)
        a = atr(h, l, c, period=14)
        # warmup sonrası
        assert abs(a.iloc[-1]) < 1e-9

    def test_increasing_volatility(self):
        h = pd.Series([10.0, 12.0, 15.0, 20.0, 25.0])
        l = pd.Series([9.0, 10.0, 11.0, 14.0, 18.0])
        c = pd.Series([10.0, 11.0, 14.0, 18.0, 24.0])
        a = atr(h, l, c, period=3)
        # son değer pozitif olmalı
        assert a.iloc[-1] > 0


class TestKlinesToDF:
    def test_parsing_and_sort(self):
        # Bybit V5: DESC sırada gelir
        klines = [
            ["1700000000000", "100", "105", "99", "104", "10", "1040"],
            ["1699999000000", "98", "101", "97", "100", "8", "808"],
        ]
        df = klines_to_df(klines)
        assert len(df) == 2
        # ASC sıralı
        assert df["timestamp"].iloc[0] < df["timestamp"].iloc[1]
        assert df["close"].iloc[0] == 100.0
        assert df["close"].iloc[1] == 104.0
        # tipler
        assert df["open"].dtype.kind == "f"

    def test_empty(self):
        df = klines_to_df([])
        assert len(df) == 0


class TestComputeAll:
    def test_all_columns_present(self):
        np.random.seed(42)
        n = 1000
        closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
        highs = closes + np.abs(np.random.randn(n) * 0.5)
        lows = closes - np.abs(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            "timestamp": range(n),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * n,
            "turnover": [1.0] * n,
        })
        out = compute_all(df, ema_period=800, donchian_period=50)
        for col in ("ema", "donchian_upper", "donchian_lower", "atr"):
            assert col in out.columns
        # son satırda hiçbiri NaN olmamalı
        assert not pd.isna(out["ema"].iloc[-1])
        assert not pd.isna(out["donchian_upper"].iloc[-1])
        assert not pd.isna(out["donchian_lower"].iloc[-1])
