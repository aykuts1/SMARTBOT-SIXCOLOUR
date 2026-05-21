"""
state.py - Acik pozisyonlari, flag'leri ve sayaclari thread-safe tutar.

Bu state main.py'deki 3 thread tarafindan paylasilir:
  - Entry thread  : flag yonetir, pozisyon ekler
  - Exit thread   : pozisyon gunceller / siler
  - Report thread : okur

Tum erisimler RLock ile korunur.
"""

import threading
from typing import Dict, List, Optional, Set

from position import Position


class StateManager:
    """Thread-safe state container."""

    def __init__(self):
        self._lock = threading.RLock()

        # Acik bot pozisyonlari: symbol -> Position
        self._positions: Dict[str, Position] = {}

        # Bot baslarken zaten acik olan disardan tespit edilen pozisyonlar
        # (bot yonetmez ama slot sayisina dahil eder)
        self._external_symbols: Set[str] = set()

        # Flag durumu: symbol -> "LONG" | "SHORT" | yoksa key yok
        self._flags: Dict[str, str] = {}

        # Sayaclar (raporlar icin)
        self._counters: Dict[str, int] = {}

        # Kapanmis islemler (raporlar icin)
        # Her kayit: {symbol, side, entry, exit, pnl, pnl_pct, exit_type,
        #             duration_min, close_time, atr_profit}
        self._closed_log: List[dict] = []

    # ----------------------------------------------------------------------
    # Pozisyon yonetimi
    # ----------------------------------------------------------------------

    def add_position(self, pos: Position) -> None:
        with self._lock:
            self._positions[pos.symbol] = pos

    def remove_position(self, symbol: str) -> Optional[Position]:
        with self._lock:
            return self._positions.pop(symbol, None)

    def get(self, symbol: str) -> Optional[Position]:
        with self._lock:
            return self._positions.get(symbol)

    def has_open_trade(self, symbol: str) -> bool:
        """Bot pozisyonu VEYA disardan tespit edilen pozisyon var mi?"""
        with self._lock:
            return symbol in self._positions or symbol in self._external_symbols

    def all_open(self) -> List[Position]:
        with self._lock:
            return list(self._positions.values())

    def open_symbols(self) -> List[str]:
        with self._lock:
            return list(self._positions.keys())

    def total_slots_used(self) -> int:
        with self._lock:
            return len(self._positions) + len(self._external_symbols)

    # ----------------------------------------------------------------------
    # External pozisyonlar (bot baslarken bulunan, manuel acilmis)
    # ----------------------------------------------------------------------

    def mark_external(self, symbol: str) -> None:
        with self._lock:
            self._external_symbols.add(symbol)

    def unmark_external(self, symbol: str) -> None:
        with self._lock:
            self._external_symbols.discard(symbol)

    def external_symbols(self) -> List[str]:
        with self._lock:
            return list(self._external_symbols)

    # ----------------------------------------------------------------------
    # Flag yonetimi
    # ----------------------------------------------------------------------

    def get_flag(self, symbol: str) -> Optional[str]:
        """Coin uzerinde flag varsa 'LONG' / 'SHORT', yoksa None."""
        with self._lock:
            return self._flags.get(symbol)

    def set_flag(self, symbol: str, side: str) -> None:
        with self._lock:
            self._flags[symbol] = side

    def clear_flag(self, symbol: str) -> Optional[str]:
        """Flag silinirse onceki yonu doner, yoksa None."""
        with self._lock:
            return self._flags.pop(symbol, None)

    # ----------------------------------------------------------------------
    # Sayaclar (raporlar icin)
    # ----------------------------------------------------------------------

    def incr(self, key: str, value: int = 1) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def get_counter(self, key: str) -> int:
        with self._lock:
            return self._counters.get(key, 0)

    def reset_counter(self, key: str) -> None:
        with self._lock:
            self._counters[key] = 0

    def snapshot_counters(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._counters)

    # ----------------------------------------------------------------------
    # Kapanmis islem kayitlari
    # ----------------------------------------------------------------------

    def log_closed(self, record: dict) -> None:
        with self._lock:
            self._closed_log.append(record)

    def closed_since(self, ts: float) -> List[dict]:
        with self._lock:
            return [r for r in self._closed_log if r.get("close_time", 0) >= ts]

    def all_closed(self) -> List[dict]:
        with self._lock:
            return list(self._closed_log)
