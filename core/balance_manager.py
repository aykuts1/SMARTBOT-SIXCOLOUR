"""Balance Manager — bakiyeyi periyodik günceller ve stake'i belirler.

Spec:
  * Bot başlatılınca toplam bakiyenin %5'i stake.
  * 50x kaldıraç.
  * Her 8 saatte bir bakiye yeniden okunur, stake güncellenir.
  * 8 saat boyunca stake sabittir.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class BalanceManager:
    def __init__(self, fetcher, stake_percent: float, update_hours: int) -> None:
        self.fetcher = fetcher
        self.stake_percent = stake_percent
        self.update_seconds = update_hours * 3600
        self._balance: float = 0.0
        self._stake: float = 0.0
        self._last_update: float = 0.0

    def force_refresh(self) -> None:
        """İlk açılışta ya da manuel tetikleme için."""
        self._balance = self.fetcher.get_wallet_balance()
        self._stake = self._balance * (self.stake_percent / 100.0)
        self._last_update = time.time()
        log.info("Bakiye: %.2f USDT, Stake: %.2f USDT",
                 self._balance, self._stake)

    def maybe_refresh(self) -> bool:
        """Süre dolduysa yenile. Yenilediyse True."""
        if time.time() - self._last_update >= self.update_seconds:
            self.force_refresh()
            return True
        return False

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def stake(self) -> float:
        return self._stake

    @property
    def last_update_ts(self) -> float:
        return self._last_update
