"""Trade Manager.

Bir işlemin yaşam döngüsü:
  1) Giriş anında seviyeler hesaplanır ve SABİTLENİR (TradeLevels).
  2) Her fiyat tick'inde:
        * Yukarı: seviye ilerletilir (geri düşmez).
        * Aşağı / EMA / WINRATE: çıkış koşulu kontrol edilir.
  3) Çıkış → kapatma sinyali döner.

LEVELS — LONG için (short ters simetri):
  d = entry - LOSE_EXIT  (entry üstündedir, d > 0)
  WINRATE = entry + 3*d
  [entry, WINRATE] aralığı 5 ara çizgi ile 6 eşit zona bölünür.
  Step = 3*d / 6 = d/2

  Lines (alttan üste):
      LOSE_EXIT
      entry         (= L0)
      ST1 line      (= entry + 1*step)
      ST2 line      (= entry + 2*step)
      ST3 line      (= entry + 3*step)
      ST4 line      (= entry + 4*step)
      ST5 line      (= entry + 5*step)
      WINRATE       (= entry + 6*step = entry + 3d)

  Zones (level):
      1 ENTRY : [entry,      ST1 line)
      2 ST1   : [ST1 line,   ST2 line)
      3 ST2   : [ST2 line,   ST3 line)
      4 ST3   : [ST3 line,   ST4 line)
      5 ST4   : [ST4 line,   ST5 line)
      6 ST5   : [ST5 line,   WINRATE)

  Exit on level (Long):
      1 ENTRY: price <= LOSE_EXIT  veya  price EMA800'e değdi
      2 ST1  : price <= LOSE_EXIT  veya  EMA
      3 ST2  : price <= entry      veya  EMA
      4 ST3  : price <= ST1 line   veya  EMA
      5 ST4  : price <= ST2 line   veya  EMA
      6 ST5  : price <= ST3 line   veya  EMA  veya  price >= WINRATE

  Short tam ters: tüm karşılaştırmalar terslenir, "üstü/altı" ve "<= / >=" ters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
import uuid


Side = Literal["long", "short"]
ExitReason = Literal["lose_exit", "ema_hit", "winrate", "trail_back", "manual"]
LEVEL_NAMES = ["ENTRY", "ST1", "ST2", "ST3", "ST4", "ST5"]  # index = level-1


@dataclass
class TradeLevels:
    side: Side
    entry: float
    lose_exit: float
    winrate: float
    # alttan üste: entry, ST1L, ST2L, ST3L, ST4L, ST5L, winrate
    # (long için "alttan üste" fiziksel olarak; short için sayısal olarak ters)
    step_lines: list[float]  # uzunluk 6 — index 0=ST1L, ... index 5=winrate
    # NOT: bu liste sırası SİDE'a göredir; "ileri yön" = kar yönü.
    # Long için step_lines[0] > entry; short için step_lines[0] < entry.

    def line_at(self, idx: int) -> float:
        """0..5: ST1 line .. WINRATE (= step_lines[5])."""
        return self.step_lines[idx]


@dataclass
class Trade:
    symbol: str
    side: Side
    size: float  # contract qty
    levels: TradeLevels
    level: int = 1  # 1..6
    opened_at_ts: int = 0
    trade_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    exchange_sl_order_id: str | None = None
    # Bilgi alanları:
    notional_usdt: float = 0.0  # girişte: size * entry
    stake_usdt: float = 0.0     # girişte: notional / leverage

    def current_level_name(self) -> str:
        return LEVEL_NAMES[self.level - 1]


# ============================================================================
# LEVEL HESAPLAMA
# ============================================================================

def compute_levels(
    side: Side,
    entry: float,
    donchian_opposite: float,
    sl_percent: float = 2.0,
    rr: float = 3.0,
) -> TradeLevels:
    """Giriş anında seviyeleri hesaplar.

    Args:
        side: 'long' veya 'short'.
        entry: market emrinin gerçekleştiği fiyat.
        donchian_opposite: long için donchian alt, short için donchian üst.
        sl_percent: %2 (notlardaki sabit).
        rr: 3 (risk:reward).
    """
    if side not in ("long", "short"):
        raise ValueError(f"side: {side}")
    if entry <= 0:
        raise ValueError("entry > 0 olmalı")
    if sl_percent <= 0 or rr <= 0:
        raise ValueError("sl_percent ve rr > 0 olmalı")

    max_loss_dist = entry * (sl_percent / 100.0)

    if side == "long":
        # LOSE_EXIT donchian altıdır; ama %2'den uzaktaysa entry*0.98'e çekilir
        if donchian_opposite >= entry:
            # Anormal durum: donchian alt entry'nin üzerinde olamaz; yine de güvenli
            # davranış: %2 stop kullan.
            lose_exit = entry - max_loss_dist
        else:
            dist = entry - donchian_opposite
            if dist > max_loss_dist:
                lose_exit = entry - max_loss_dist
            else:
                lose_exit = donchian_opposite

        d = entry - lose_exit
        winrate = entry + rr * d
        # 6 eşit zona böl → 5 ara çizgi
        step = (winrate - entry) / 6.0
        step_lines = [entry + (i + 1) * step for i in range(6)]
        # step_lines[0..4] = ST1L..ST5L,  step_lines[5] = winrate
    else:  # short
        if donchian_opposite <= entry:
            lose_exit = entry + max_loss_dist
        else:
            dist = donchian_opposite - entry
            if dist > max_loss_dist:
                lose_exit = entry + max_loss_dist
            else:
                lose_exit = donchian_opposite

        d = lose_exit - entry
        winrate = entry - rr * d
        step = (entry - winrate) / 6.0
        step_lines = [entry - (i + 1) * step for i in range(6)]

    return TradeLevels(
        side=side,
        entry=float(entry),
        lose_exit=float(lose_exit),
        winrate=float(winrate),
        step_lines=[float(x) for x in step_lines],
    )


# ============================================================================
# SEVIYE TAKİBİ
# ============================================================================

def _is_advanced(side: Side, price: float, line: float) -> bool:
    """Fiyat line'ı 'ileri yönde' geçti mi?"""
    return price >= line if side == "long" else price <= line


def update_level(trade: Trade, price: float) -> int | None:
    """Fiyat tick'i ile yeni seviyeye atla. Atlandıysa yeni seviyeyi döner.

    Mantık: mevcut seviye=k. step_lines[k-1] = bir sonraki çizgi (1-indexed
    seviye=k için bir sonraki çizgi index k-1). Atlanan en yüksek çizgiye kadar
    seviye ilerletilir.
    """
    new_level = trade.level
    while new_level < 6:
        next_line = trade.levels.step_lines[new_level - 1]
        # new_level = 1 → step_lines[0] = ST1 line, geçilirse level=2 (ST1)
        # new_level = 5 → step_lines[4] = ST5 line, geçilirse level=6 (ST5)
        # new_level = 6 → durur (winrate kontrolü exit'te yapılır)
        if _is_advanced(trade.levels.side, price, next_line):
            new_level += 1
        else:
            break
    if new_level != trade.level:
        trade.level = new_level
        return new_level
    return None


# ============================================================================
# ÇIKIŞ KONTROLÜ
# ============================================================================

def _exit_trigger_line_for_level(trade: Trade) -> float:
    """Mevcut seviye için 'geriye dönüş' çizgisini döner.

    Long:
        Level 1 (ENTRY): LOSE_EXIT
        Level 2 (ST1):   LOSE_EXIT
        Level 3 (ST2):   ENTRY
        Level 4 (ST3):   ST1 line  = step_lines[0]
        Level 5 (ST4):   ST2 line  = step_lines[1]
        Level 6 (ST5):   ST3 line  = step_lines[2]
    """
    L = trade.level
    if L in (1, 2):
        return trade.levels.lose_exit
    if L == 3:
        return trade.levels.entry
    # L = 4,5,6 → ST(L-3) line  → step_lines[L-4]
    return trade.levels.step_lines[L - 4]


def _hit_exit_line(side: Side, price: float, line: float) -> bool:
    """Geriye dönüş — long için price<=line, short için price>=line."""
    return price <= line if side == "long" else price >= line


def _hit_winrate(side: Side, price: float, winrate: float) -> bool:
    return price >= winrate if side == "long" else price <= winrate


def check_exit(trade: Trade, price: float, ema_val: float | None,
               prev_price: float | None = None) -> ExitReason | None:
    """Çıkış koşulu kontrol et. Çıkış varsa sebebini döner."""
    side = trade.levels.side

    # 1) EMA800'e çarpma — her seviyede aktif
    if ema_val is not None and not _is_nan(ema_val):
        if prev_price is None:
            # tolerans: çok yakınsa değdi say
            if abs(price - ema_val) / max(ema_val, 1e-9) < 1e-6:
                return "ema_hit"
        else:
            # ardışık iki tick arasında EMA geçildi mi?
            if (prev_price - ema_val) * (price - ema_val) <= 0:
                return "ema_hit"

    # 2) WINRATE — sadece level 6
    if trade.level == 6 and _hit_winrate(side, price, trade.levels.winrate):
        return "winrate"

    # 3) Geri dönüş çizgisi
    exit_line = _exit_trigger_line_for_level(trade)
    if _hit_exit_line(side, price, exit_line):
        # Level 1-2 → lose_exit; diğerleri → trail
        if trade.level in (1, 2):
            return "lose_exit"
        return "trail_back"

    return None


def _is_nan(x: float) -> bool:
    return x != x  # NaN check


# ============================================================================
# PnL HESAPLAMA (raporlama için)
# ============================================================================

def unrealized_pnl_usdt(trade: Trade, price: float) -> float:
    """Açık PnL (USDT). Linear futures varsayımı."""
    if trade.levels.side == "long":
        return (price - trade.levels.entry) * trade.size
    return (trade.levels.entry - price) * trade.size


def unrealized_pnl_pct(trade: Trade, price: float) -> float:
    """Stake'e göre %. Notional / stake = leverage olduğundan, hareket * leverage."""
    if trade.stake_usdt <= 0:
        return 0.0
    return unrealized_pnl_usdt(trade, price) / trade.stake_usdt * 100.0
