"""
price_history.py - Her coin icin son HISTORY_SECONDS saniyelik fiyat gecmisini tutar.

Kullanim:
  history.add(symbol, price)          -> fiyat ekle, eskiyen temizle
  history.last(symbol)                -> son eklenen fiyat (None = hic eklenmemis)
  history.prev(symbol)                -> sondan bir onceki fiyat (crossover tespiti icin)
  history.count(symbol)               -> gecmisteki fiyat adedi
"""

import time
import threading
from collections import deque
from typing import Optional, Tuple


class PriceHistory:
    def __init__(self, history_seconds: int):
        self._history_seconds = history_seconds
        self._lock = threading.Lock()
        # symbol -> deque of (timestamp, price)
        self._data: dict = {}

    def _ensure(self, symbol: str) -> deque:
        if symbol not in self._data:
            self._data[symbol] = deque()
        return self._data[symbol]

    def add(self, symbol: str, price: float) -> None:
        """Fiyat ekle, HISTORY_SECONDS'dan eski kayitlari temizle."""
        now = time.time()
        cutoff = now - self._history_seconds
        with self._lock:
            dq = self._ensure(symbol)
            dq.append((now, price))
            # Eski kayitlari sol taraftan temizle
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    def last(self, symbol: str) -> Optional[float]:
        """Son eklenen fiyat. Hic eklenmemisse None."""
        with self._lock:
            dq = self._data.get(symbol)
            if not dq:
                return None
            return dq[-1][1]

    def prev(self, symbol: str) -> Optional[float]:
        """Sondan bir onceki fiyat. Crossover tespiti icin kullanilir.
        En az 2 kayit yoksa None doner."""
        with self._lock:
            dq = self._data.get(symbol)
            if not dq or len(dq) < 2:
                return None
            return dq[-2][1]

    def count(self, symbol: str) -> int:
        """Gecmisteki fiyat adedi."""
        with self._lock:
            dq = self._data.get(symbol)
            return len(dq) if dq else 0
