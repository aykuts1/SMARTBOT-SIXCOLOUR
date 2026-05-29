"""Bybit V5 API — sadece okuma (kline, ticker, balance).

pybit kütüphanesini kullanır. Trade işlemleri order_manager'da.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pybit.unified_trading import HTTP

log = logging.getLogger(__name__)


class BybitDataFetcher:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False,
                 retry_count: int = 3, retry_delay: int = 5) -> None:
        self.client = HTTP(api_key=api_key, api_secret=api_secret, testnet=testnet)
        self.retry_count = retry_count
        self.retry_delay = retry_delay

    def _with_retry(self, fn, *args, **kwargs) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.retry_count):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_exc = e
                log.warning("Bybit API hatası (deneme %d/%d): %s",
                            attempt + 1, self.retry_count, e)
                if attempt < self.retry_count - 1:
                    time.sleep(self.retry_delay)
        raise last_exc  # type: ignore[misc]

    def get_klines(self, symbol: str, interval: str = "15",
                   limit: int = 1000) -> list:
        """Linear futures (USDT perpetual) kline.

        Bybit V5: DESC sırada döner. klines_to_df bunu ASC'ye çevirir.
        Açık (kapanmamış) mumu bot mantığında kullanmak istemiyoruz; çağıran
        en son satırı drop edebilir.
        """
        resp = self._with_retry(
            self.client.get_kline,
            category="linear", symbol=symbol, interval=interval, limit=limit,
        )
        return resp.get("result", {}).get("list", [])

    def get_ticker_price(self, symbol: str) -> float:
        """Son işlem fiyatı (lastPrice)."""
        resp = self._with_retry(
            self.client.get_tickers, category="linear", symbol=symbol,
        )
        items = resp.get("result", {}).get("list", [])
        if not items:
            raise RuntimeError(f"{symbol} ticker bulunamadı")
        return float(items[0]["lastPrice"])

    def get_wallet_balance(self, account_type: str = "UNIFIED",
                           coin: str = "USDT") -> float:
        """Cüzdandaki USDT bakiyesi (totalWalletBalance veya availableBalance)."""
        resp = self._with_retry(
            self.client.get_wallet_balance,
            accountType=account_type, coin=coin,
        )
        items = resp.get("result", {}).get("list", [])
        if not items:
            return 0.0
        # UNIFIED: account-level walletBalance
        for entry in items:
            for c in entry.get("coin", []):
                if c.get("coin") == coin:
                    # availableToWithdraw daha güvenli; yoksa walletBalance
                    val = c.get("availableToWithdraw") or c.get("walletBalance") or "0"
                    return float(val)
        return 0.0

    def get_positions(self, symbol: str | None = None) -> list[dict]:
        """Açık pozisyonlar.

        Bybit "one-way mode" vs "hedge mode" farkına dikkat. Bot 'hedge mode'
        kullanmalı (long ve short aynı anda) — bunu order_manager'da set ediyoruz.
        """
        kwargs: dict = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            kwargs["symbol"] = symbol
        resp = self._with_retry(self.client.get_positions, **kwargs)
        return resp.get("result", {}).get("list", [])

    def get_instruments_info(self, symbol: str) -> dict:
        """Sembol bilgisi: min qty, tick size, leverage filtreleri."""
        resp = self._with_retry(
            self.client.get_instruments_info, category="linear", symbol=symbol,
        )
        items = resp.get("result", {}).get("list", [])
        return items[0] if items else {}
