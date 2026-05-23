"""
position.py - Pozisyon state'i, seviye gecisleri, cikis kontrolu.

Seviyeler:
  LEVEL_ENTRY    (0) : Giris yapildi, henuz BE yok
  LEVEL_BE       (1) : Fiyat dis tamponu gecti, BE aktif. BE cikis cizgisi
                       anlik disbantla beraber hareket eder (DINAMIK).
  LEVEL_CE1      (2) : Kar >= CE1_ATR. Chandelier CE1_TRAIL ATR geriden takip.
  LEVEL_CE2      (3) : Kar >= CE2_ATR. Chandelier CE2_TRAIL ATR geriden takip.
  LEVEL_WINRATE  (4) : Kar >= WINRATE_ATR. Chandelier WINRATE_TRAIL ATR geriden.

Cikis tipleri (oncelik sirasi yuksekten dusuge):
  CE Exit (CE1/CE2/Winrate Exit) : Chandelier seviyesine carpildi
  BE Exit                        : Dinamik BE cizgisinin (= disbant) ic tarafina dustu
  LOSE Exit                      : Ic tamponun ic tarafina dustu
  Stoploss Exit                  : Borsa %1 SL emrini tetikledi (disardan tespit edilir)

ONEMLI KURAL: Ust seviyeye gecildiginde alt seviyelerin cikislari da hala aktif kalir.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


# Seviye sabitleri
LEVEL_ENTRY   = 0
LEVEL_BE      = 1
LEVEL_CE1     = 2
LEVEL_WINRATE = 3

LEVEL_LABELS = {
    LEVEL_ENTRY:   "Giris",
    LEVEL_BE:      "BE",
    LEVEL_CE1:     "CE1",
    LEVEL_WINRATE: "Winrate",
}


@dataclass
class Position:
    """Bot tarafindan acilan ve takip edilen pozisyon."""
    symbol:          str
    side:            str       # "LONG" / "SHORT"
    entry_price:     float
    qty:             float
    stake:           float     # USDT teminat
    notional:        float     # entry_price * qty
    leverage:        int
    atr_at_entry:    float
    stop_loss_price: float

    open_time:       float = field(default_factory=time.time)

    # Seviye state
    level:           int = LEVEL_ENTRY

    # CE takibi
    best_price:      float = 0.0       # long: max gorulen, short: min gorulen
    ce_price:        Optional[float] = None   # mevcut chandelier cikis seviyesi

    # BE cikis cizgisi (dinamik - her tarama guncellenir, = anlik disbant)
    be_exit_price:   Optional[float] = None

    def __post_init__(self):
        if self.best_price == 0.0:
            self.best_price = self.entry_price

    # --- Kar/zarar hesaplari -------------------------------------------------

    def update_best(self, price: float) -> None:
        if self.side == "LONG":
            if price > self.best_price:
                self.best_price = price
        else:
            if price < self.best_price:
                self.best_price = price

    def profit_in_atr(self, price: float) -> float:
        if self.atr_at_entry <= 0:
            return 0.0
        if self.side == "LONG":
            diff = price - self.entry_price
        else:
            diff = self.entry_price - price
        return diff / self.atr_at_entry

    def profit_pct(self, price: float) -> float:
        """Kaldirassiz yuzdesel kar."""
        if self.side == "LONG":
            return (price - self.entry_price) / self.entry_price * 100.0
        else:
            return (self.entry_price - price) / self.entry_price * 100.0

    def profit_pct_leveraged(self, price: float) -> float:
        """Kaldiracli yuzdesel kar (stake'e gore)."""
        return self.profit_pct(price) * self.leverage

    def profit_usdt(self, price: float) -> float:
        """USDT cinsinden net kar/zarar."""
        if self.side == "LONG":
            return (price - self.entry_price) * self.qty
        else:
            return (self.entry_price - price) * self.qty


def _compute_ce(pos: Position, trail_atr: float) -> float:
    """CE = best_price -/+ trail * ATR."""
    trail = trail_atr * pos.atr_at_entry
    if pos.side == "LONG":
        return pos.best_price - trail
    return pos.best_price + trail


def update_level_and_ce(
    pos: Position,
    price: float,
    ust_dis_tampon: float,
    alt_dis_tampon: float,
    ust_disbant:    float,
    alt_disbant:    float,
    ce1_atr: float, ce1_trail: float,
    ce2_atr: float = 0.0, ce2_trail: float = 0.0,   # KULLANILMIYOR - geriye uyumluluk icin
    winrate_atr: float = 5.0, winrate_trail: float = 0.5,
) -> Optional[int]:
    """
    Her tarama dongusunde cagrilir. Su islemleri yapar:

    1. best_price'i gunceller
    2. BE seviyesini kontrol eder (fiyat dis tamponu gecti mi?)
    3. BE cikis cizgisini DINAMIK olarak anlik disbant'a esitler
    4. CE1, WINRATE seviyelerini kontrol eder, CE'yi gunceller
    5. Yeni bir seviyeye geciliyorsa o seviyenin numarasini doner, yoksa None

    Yeni mantik: Her seviyeye gecildiginde eski CE chandelier'i sifirlanir.
    CE2 seviyesi KALDIRILDI - CE1'den dogrudan WINRATE'e gecilir.
    """
    pos.update_best(price)
    new_level = pos.level

    # --- BE seviye gecisi ---
    if pos.level < LEVEL_BE:
        if pos.side == "LONG" and price > ust_dis_tampon:
            new_level = LEVEL_BE
        elif pos.side == "SHORT" and price < alt_dis_tampon:
            new_level = LEVEL_BE

    # --- BE cikis cizgisi (DINAMIK: anlik disbant) ---
    if new_level >= LEVEL_BE or pos.level >= LEVEL_BE:
        pos.be_exit_price = ust_disbant if pos.side == "LONG" else alt_disbant

    # --- CE seviye gecisleri ---
    profit_atr = pos.profit_in_atr(pos.best_price)

    if profit_atr >= winrate_atr and pos.level < LEVEL_WINRATE:
        new_level = LEVEL_WINRATE
    elif profit_atr >= ce1_atr and pos.level < LEVEL_CE1:
        new_level = LEVEL_CE1

    # --- Aktif CE takip carpani ---
    if new_level >= LEVEL_WINRATE:
        trail = winrate_trail
    elif new_level >= LEVEL_CE1:
        trail = ce1_trail
    else:
        trail = None

    # --- Yeni bir CE seviyesine gecildiyse eski CE'yi sifirla ---
    # WINRATE'e gecince eski CE1 chandelier'i kullanilmaz.
    if new_level != pos.level and new_level >= LEVEL_CE1:
        pos.ce_price = None

    # --- CE guncelleme (ayni seviye icinde asla geri cekilmez) ---
    if trail is not None:
        candidate = _compute_ce(pos, trail)
        if pos.ce_price is None:
            pos.ce_price = candidate
        else:
            if pos.side == "LONG":
                pos.ce_price = max(pos.ce_price, candidate)
            else:
                pos.ce_price = min(pos.ce_price, candidate)

    if new_level != pos.level:
        pos.level = new_level
        return new_level
    return None


def check_exit(
    pos: Position,
    price: float,
    ust_ic_tampon: float,
    alt_ic_tampon: float,
) -> Optional[str]:
    """
    Cikis tetikleyicilerini kontrol eder.

    YENI MANTIK: Her seviyede SADECE o seviyenin cikisi aktiftir.
    Ust seviyeye gecildiginde alt seviyenin cikisi DEVRE DISI kalir.
      - ENTRY  seviyesi -> LOSE EXIT (ic tampon)
      - BE     seviyesi -> BE EXIT (dinamik disbant)
      - CE1    seviyesi -> CE1 EXIT (chandelier)
      - WINRATE seviyesi -> WINRATE EXIT (chandelier)

    Borsa %1 SL emri her zaman aktiftir, disardan tetiklenir.
    """
    # CE seviyelerinde sadece CE Exit aktif
    if pos.level >= LEVEL_CE1:
        if pos.ce_price is not None:
            if pos.side == "LONG" and price <= pos.ce_price:
                return _ce_exit_name(pos.level)
            if pos.side == "SHORT" and price >= pos.ce_price:
                return _ce_exit_name(pos.level)
        return None

    # BE seviyesinde sadece BE Exit aktif
    if pos.level == LEVEL_BE:
        if pos.be_exit_price is not None:
            if pos.side == "LONG" and price <= pos.be_exit_price:
                return "BE Exit"
            if pos.side == "SHORT" and price >= pos.be_exit_price:
                return "BE Exit"
        return None

    # ENTRY seviyesinde sadece LOSE Exit aktif
    if pos.side == "LONG" and price < ust_ic_tampon:
        return "Lose Exit"
    if pos.side == "SHORT" and price > alt_ic_tampon:
        return "Lose Exit"

    return None


def _ce_exit_name(level: int) -> str:
    if level == LEVEL_WINRATE:
        return "Winrate Exit"
    if level == LEVEL_CE1:
        return "CE1 Exit"
    return "Lose Exit"


def next_level_target(
    pos: Position,
    ce1_atr: float, ce2_atr: float, winrate_atr: float,   # ce2_atr KULLANILMIYOR
) -> Optional[tuple]:
    """
    Bir sonraki seviyenin (label, hedef_fiyat). Telegram raporlarinda gosterilir.
    En son seviyedeyse None.
    """
    atr = pos.atr_at_entry
    if pos.level >= LEVEL_WINRATE:
        return None
    elif pos.level >= LEVEL_CE1:
        label = "Winrate"
        target_atr = winrate_atr
    else:
        # ENTRY veya BE seviyesinde -> sonraki CE1
        label = "CE1"
        target_atr = ce1_atr

    if pos.side == "LONG":
        target_price = pos.entry_price + target_atr * atr
    else:
        target_price = pos.entry_price - target_atr * atr

    return (label, target_price)
