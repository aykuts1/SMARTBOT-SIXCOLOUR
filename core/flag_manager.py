"""Flag Manager.

15dk mum kapanışında çağrılır. Her coin için son 2 kapalı mum üzerinden
Donchian üst/alt çizgilerinin değişimine bakar; flag açar/koruma kuralları uygular.

Spec özet:
- Long Flag: Donchian üst, bir önceki mum kapanışından AŞAĞI düştüyse.
- Short Flag: Donchian alt, bir önceki mum kapanışından YUKARI çıktıysa.
- EMA800 trend filtresi GİRİŞ anında uygulanır (main.py içinde), flag açılışında değil.
- Flag silinme:
    * İşlem açılınca silinir (trade_manager'da yönetilir; burada sadece state'i tutarız).
    * Fiyat EMA800'e çarparsa silinir (her flag kontrolünde tarama).
    * İşlem açıkken tekrar flag koşulu oluşursa yeni flag açılır.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


FlagSide = Literal["long", "short"]


@dataclass
class Flag:
    symbol: str
    side: FlagSide
    created_at_ts: int  # mum kapanış timestamp (ms)
    donchian_value: float  # flag açıldığı andaki ilgili donchian değeri (üst/alt)
    entry_trigger_price: float  # tetik fiyatı = donchian değeri


def detect_flag_signal(df: pd.DataFrame) -> FlagSide | None:
    """Kapanmış son 2 mum üzerinden flag sinyali döner.

    df: indicators.compute_all geçmiş, son satır EN YENİ KAPANMIŞ mum.
    EMA800 trend filtresi BURADA UYGULANMAZ — giriş anında main.py içinde
    fiyat-EMA karşılaştırması yapılır.

    Sinyal:
        * "long"  → donchian_upper[-1] < donchian_upper[-2]
        * "short" → donchian_lower[-1] > donchian_lower[-2]
        * None    → koşul yok.
    """
    if len(df) < 2:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    # NaN kontrolü — yeterli veri yoksa sinyal yok
    needed = [
        last["donchian_upper"], last["donchian_lower"],
        prev["donchian_upper"], prev["donchian_lower"],
    ]
    if any(pd.isna(x) for x in needed):
        return None

    upper_dropped = float(last["donchian_upper"]) < float(prev["donchian_upper"])
    lower_rose = float(last["donchian_lower"]) > float(prev["donchian_lower"])

    if upper_dropped:
        return "long"
    if lower_rose:
        return "short"
    return None


def price_crossed_ema(prev_close: float, last_close: float, ema_val: float) -> bool:
    """Fiyat EMA800'e çarptı mı? İki kapanış EMA'nın zıt taraflarındaysa veya
    biri EMA'ya tam değdiyse True.

    Not: Bot 5sn'de bir fiyat çekiyor; daha hassas tetiklemek için canlı fiyat ile
    de bu fonksiyonu çağırabiliriz. Burada genel "geçti mi" kontrolü.
    """
    if any(pd.isna(x) for x in [prev_close, last_close, ema_val]):
        return False
    return (prev_close - ema_val) * (last_close - ema_val) <= 0


class FlagManager:
    """Her coin için tek bir aktif long ve tek bir aktif short flag tutar.

    state: { symbol: { "long": Flag | None, "short": Flag | None } }
    """

    def __init__(self, state: dict | None = None) -> None:
        self.state: dict[str, dict[str, Flag | None]] = {}
        if state:
            self.load(state)

    # ------------- core API -------------

    def on_candle_close(self, symbol: str, df: pd.DataFrame) -> Flag | None:
        """15dk kapanışta çağır. Yeni flag oluştuysa onu döner, yoksa None.

        Mevcut flag varsa ÜZERİNE yazılır (spec: 'İşlem açıkken tekrar flag
        koşulu oluşursa yeni flag açılır').
        """
        signal = detect_flag_signal(df)
        if signal is None:
            return self._clear_flag_if_price_crossed_ema(symbol, df)

        last = df.iloc[-1]
        if signal == "long":
            trigger = float(last["donchian_upper"])
        else:
            trigger = float(last["donchian_lower"])

        flag = Flag(
            symbol=symbol,
            side=signal,
            created_at_ts=int(last["timestamp"]),
            donchian_value=trigger,
            entry_trigger_price=trigger,
        )
        self.state.setdefault(symbol, {"long": None, "short": None})
        self.state[symbol][signal] = flag
        return flag

    def on_price_tick(self, symbol: str, price: float, ema_val: float) -> list[FlagSide]:
        """5sn fiyat tarama: fiyat EMA'ya çarparsa flag(ler) silinir.

        Bu fonksiyon silinen flag taraflarını döner (long/short).
        """
        removed: list[FlagSide] = []
        if symbol not in self.state:
            return removed
        coin_state = self.state[symbol]
        # Fiyat EMA'ya değerse (tolerans yok — kesin değme): hangi tarafta olduğuna
        # göre o yöndeki flag silinir? Spec: "Fiyat EMA800'e çarparsa silinir"
        # — yön ayrımı yapmıyor. İki tarafı da temizliyoruz.
        # Praktik: tam eşitlik float'ta nadirdir; fiyat EMA'ya çok yakınsa kabul.
        if abs(price - ema_val) / max(ema_val, 1e-9) < 1e-6:
            for side in ("long", "short"):
                if coin_state.get(side) is not None:
                    coin_state[side] = None
                    removed.append(side)  # type: ignore[arg-type]
        return removed

    def on_price_cross_ema(self, symbol: str, prev_price: float,
                           cur_price: float, ema_val: float) -> list[FlagSide]:
        """İki ardışık fiyat noktası arasında EMA geçildiyse flag(ler) silinir."""
        removed: list[FlagSide] = []
        if symbol not in self.state:
            return removed
        if price_crossed_ema(prev_price, cur_price, ema_val):
            for side in ("long", "short"):
                if self.state[symbol].get(side) is not None:
                    self.state[symbol][side] = None
                    removed.append(side)  # type: ignore[arg-type]
        return removed

    def check_entry_trigger(self, symbol: str, price: float) -> FlagSide | None:
        """Aktif flag var ve fiyat tetik çizgisine değdi mi?

        Long flag → fiyat donchian üste >= değdi (yukarı break veya wick).
        Short flag → fiyat donchian alta <= değdi.
        """
        coin_state = self.state.get(symbol)
        if not coin_state:
            return None
        long_flag = coin_state.get("long")
        if long_flag is not None and price >= long_flag.entry_trigger_price:
            return "long"
        short_flag = coin_state.get("short")
        if short_flag is not None and price <= short_flag.entry_trigger_price:
            return "short"
        return None

    def consume_flag(self, symbol: str, side: FlagSide) -> Flag | None:
        """İşlem açıldı — flag'i tüket (sil) ve döndür."""
        coin_state = self.state.get(symbol)
        if not coin_state:
            return None
        flag = coin_state.get(side)
        coin_state[side] = None
        return flag

    def get_flag(self, symbol: str, side: FlagSide) -> Flag | None:
        return self.state.get(symbol, {}).get(side)

    def active_flags(self) -> list[Flag]:
        out = []
        for sym, sides in self.state.items():
            for side in ("long", "short"):
                f = sides.get(side)
                if f is not None:
                    out.append(f)
        return out

    # ------------- persistence -------------

    def dump(self) -> dict:
        out: dict = {}
        for sym, sides in self.state.items():
            out[sym] = {}
            for side in ("long", "short"):
                f = sides.get(side)
                out[sym][side] = None if f is None else {
                    "symbol": f.symbol,
                    "side": f.side,
                    "created_at_ts": f.created_at_ts,
                    "donchian_value": f.donchian_value,
                    "entry_trigger_price": f.entry_trigger_price,
                }
        return out

    def load(self, data: dict) -> None:
        self.state = {}
        for sym, sides in data.items():
            self.state[sym] = {"long": None, "short": None}
            for side in ("long", "short"):
                raw = sides.get(side)
                if raw is None:
                    continue
                self.state[sym][side] = Flag(
                    symbol=raw["symbol"],
                    side=raw["side"],
                    created_at_ts=raw["created_at_ts"],
                    donchian_value=raw["donchian_value"],
                    entry_trigger_price=raw["entry_trigger_price"],
                )

    # ------------- internal -------------

    def _clear_flag_if_price_crossed_ema(self, symbol: str, df: pd.DataFrame) -> None:
        if len(df) < 2:
            return None
        last = df.iloc[-1]
        prev = df.iloc[-2]
        if pd.isna(last["ema"]):
            return None
        if price_crossed_ema(float(prev["close"]), float(last["close"]),
                             float(last["ema"])):
            self.state.setdefault(symbol, {"long": None, "short": None})
            for side in ("long", "short"):
                self.state[symbol][side] = None
        return None
