"""State persistence + entegrasyon (mock) testleri."""

import os
import tempfile
import math

import pytest

from core.state import save_state, load_state
from core.trade_manager import (
    Trade, compute_levels, update_level, check_exit,
    unrealized_pnl_usdt,
)
from core.flag_manager import FlagManager
import pandas as pd


class TestStatePersistence:
    def test_save_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "state.json")
        # Bir long trade oluştur
        levels = compute_levels("long", 100.0, 99.0)
        t = Trade(
            symbol="BTCUSDT", side="long", size=0.5, levels=levels,
            level=3, opened_at_ts=1700000000000,
            notional_usdt=50.0, stake_usdt=1.0,
            exchange_sl_order_id="xyz123",
        )
        save_state(path, [t], {}, {"foo": "bar"})

        trades2, flags2, stats2 = load_state(path)
        assert len(trades2) == 1
        t2 = trades2[0]
        assert t2.symbol == "BTCUSDT"
        assert t2.side == "long"
        assert t2.size == 0.5
        assert t2.level == 3
        assert t2.exchange_sl_order_id == "xyz123"
        assert math.isclose(t2.levels.entry, 100.0)
        assert math.isclose(t2.levels.lose_exit, 99.0)
        assert math.isclose(t2.levels.winrate, 103.0)
        assert len(t2.levels.step_lines) == 6
        assert stats2 == {"foo": "bar"}

    def test_load_nonexistent_returns_empty(self, tmp_path):
        path = str(tmp_path / "nope.json")
        trades, flags, stats = load_state(path)
        assert trades == []
        assert flags == {}
        assert stats == {}

    def test_corrupted_file_is_backed_up(self, tmp_path):
        path = str(tmp_path / "state.json")
        with open(path, "w") as f:
            f.write("{ NOT VALID JSON")
        trades, flags, stats = load_state(path)
        assert trades == []
        # Bozuk dosya yedeklenmiş olmalı
        siblings = list(tmp_path.iterdir())
        assert any(".broken." in p.name for p in siblings)

    def test_atomic_write(self, tmp_path):
        """Atomic write: temp dosya kalmaz."""
        path = str(tmp_path / "state.json")
        save_state(path, [], {}, {})
        # Temp dosya kalmamalı
        siblings = [p.name for p in tmp_path.iterdir()]
        assert "state.json" in siblings
        assert not any(s.startswith(".state_") for s in siblings)

    def test_save_with_flags(self, tmp_path):
        path = str(tmp_path / "state.json")
        df = pd.DataFrame([
            (1, 100, 105, 99, 102, 95, 110, 95),
            (2, 102, 106, 100, 104, 96, 108, 96),
        ], columns=[
            "timestamp", "open", "high", "low", "close",
            "ema", "donchian_upper", "donchian_lower",
        ])
        fm = FlagManager()
        fm.on_candle_close("BTCUSDT", df)

        save_state(path, [], fm.dump(), {})
        _, flags2, _ = load_state(path)
        fm2 = FlagManager(state=flags2)
        f = fm2.get_flag("BTCUSDT", "long")
        assert f is not None
        assert f.entry_trigger_price == 108


# ============================================================================
# UÇTAN UCA SİMÜLASYON — gerçek bir senaryoda her şey doğru mu?
# ============================================================================

class TestEndToEndScenarios:
    """Gerçek fiyat hareketi simülasyonu — flag aç, gir, takip et, çıkış."""

    def _build_history(self):
        """Donchian üst düşmesini ve close>ema'yı garantileyen veri seti."""
        ts = list(range(1000))
        # 1000 mumluk veri: ilk 948 yatay 100, son 52 hafif yukarı.
        closes = [100.0] * 1000
        # Son 50 mum penceresi: indices 950..999 → bu pencerede peak high YOK.
        # Önceki mum penceresi: indices 949..998 → bu pencerede 949'daki peak high VAR.
        # → Donchian üst düşer (önceki yüksek, sondaki düşük).
        # close>ema için son ~50 mumda fiyat 100'ün biraz üstüne çıksın.
        for i in range(950, 1000):
            closes[i] = 100.5
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        # PEAK SPIKE: index 948-949'da yüksek high → son pencereden çıkacak
        highs[948] = 102.0
        highs[949] = 102.5
        return pd.DataFrame({
            "timestamp": ts,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * 1000,
            "turnover": [1.0] * 1000,
        })

    def test_full_long_lifecycle(self):
        """Senaryo: flag açılır, giriş, ST3'e tırmanır, sonra geri düşer → trail exit."""
        from core.indicators import compute_all

        df = self._build_history()
        df = compute_all(df, ema_period=800, donchian_period=50)
        last_close = float(df.iloc[-1]["close"])
        last_ema = float(df.iloc[-1]["ema"])

        # EMA'nın altında olmalı ki long flag oluşmasın? Tam tersi: EMA üstünde olmalı
        # Trend yukarı olduğundan EMA gecikecek ve close > EMA olacak.
        assert last_close > last_ema, "Setup: close ema üstünde olmalı"

        # Flag manager
        fm = FlagManager()
        flag = fm.on_candle_close("BTCUSDT", df.tail(2).reset_index(drop=True))
        # Donchian üst düşmüşse long flag açılmalı
        assert flag is not None, "Flag oluşmadı — setup hatası"
        assert flag.side == "long"

        # Tetik fiyatına gel → giriş
        trigger = flag.entry_trigger_price
        side = fm.check_entry_trigger("BTCUSDT", trigger)
        assert side == "long"

        # Entry oluştur
        entry_price = trigger
        donchian_lower = float(df.iloc[-1]["donchian_lower"])
        levels = compute_levels("long", entry_price, donchian_lower,
                                sl_percent=2.0, rr=3.0)
        t = Trade(symbol="BTCUSDT", side="long", size=1.0, levels=levels,
                  notional_usdt=entry_price, stake_usdt=entry_price * 0.02)
        fm.consume_flag("BTCUSDT", "long")

        # Fiyat ST3 (level 4) seviyesine kadar yükselsin
        st3_price = levels.step_lines[2]  # ST3 line
        update_level(t, st3_price)
        assert t.level == 4

        # Geriye düşüş: ST1 line altına → trail_back
        st1_price = levels.step_lines[0]
        exit_reason = check_exit(t, st1_price, ema_val=None)
        assert exit_reason == "trail_back"

        # PnL — kâr olmalı (ST1 line > entry)
        pnl = unrealized_pnl_usdt(t, st1_price)
        assert pnl > 0, f"ST1 exit'te kâr beklendi, oldu: {pnl}"

    def test_three_trades_same_coin_same_direction(self):
        """Spec: aynı coinde aynı yönde max 3 işlem."""
        levels = compute_levels("long", 100.0, 99.0)
        trades = [
            Trade(symbol="BTCUSDT", side="long", size=1.0, levels=levels)
            for _ in range(3)
        ]
        # Her trade bağımsız — biri kapanırken diğeri etkilenmemeli
        trades[0].level = 6
        trades[1].level = 3
        trades[2].level = 1
        # Her birine farklı exit aramalısın
        for t in trades:
            assert isinstance(t.trade_id, str)
            assert len(t.trade_id) == 12
        # Trade ID'ler farklı
        ids = {t.trade_id for t in trades}
        assert len(ids) == 3

    def test_exchange_sl_at_2_percent_long(self):
        """Borsa SL = entry * 0.98 (long), entry * 1.02 (short)."""
        # Donchian alt entry'den uzaksa LOSE_EXIT %2'ye çekilir
        levels = compute_levels("long", 100.0, 90.0)  # uzak donchian
        assert math.isclose(levels.lose_exit, 98.0)

        levels_s = compute_levels("short", 100.0, 110.0)
        assert math.isclose(levels_s.lose_exit, 102.0)

    def test_winrate_distance_is_3x(self):
        """WINRATE - entry = 3 * (entry - LOSE_EXIT)."""
        levels = compute_levels("long", 100.0, 99.5)  # d=0.5
        # 3 * 0.5 = 1.5; winrate = 101.5
        assert math.isclose(levels.winrate, 101.5)

    def test_step_size_equals_d_over_2(self):
        """6 eşit zona bölündüğüne göre step = d/2."""
        levels = compute_levels("long", 100.0, 99.0)  # d=1
        # Her step = 0.5
        for i in range(1, len(levels.step_lines)):
            diff = levels.step_lines[i] - levels.step_lines[i-1]
            assert math.isclose(diff, 0.5), f"step {i}: {diff}"
        # İlk step entry'ye göre
        assert math.isclose(levels.step_lines[0] - levels.entry, 0.5)
