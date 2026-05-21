"""
bybit.py - Bybit Unified Trading v5 API wrapper (pybit kullanir).

Sadece bot icin gerekli fonksiyonlar:
  - Hesap bakiyesi
  - Instrument bilgileri (tick, qty step, max leverage)
  - Isolated mod ve kaldirac ayari
  - Kline & son fiyat
  - Acik pozisyonlar
  - Limit (post-only) emir
  - Market emir (SADECE cikis fallback'i icin)
  - Borsa-tarafli stop loss
"""

import time
from typing import Dict, List, Optional

from pybit.unified_trading import HTTP


ACCOUNT_TYPE = "UNIFIED"
CATEGORY     = "linear"   # USDT perpetual


class BybitClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.session = HTTP(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            recv_window=20000,
        )
        self._instrument_cache: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Yardimcilar
    # ------------------------------------------------------------------

    @staticmethod
    def _check(resp: dict, label: str) -> dict:
        if not isinstance(resp, dict):
            raise RuntimeError(f"{label}: gecersiz yanit: {resp}")
        if resp.get("retCode") not in (0, None):
            raise RuntimeError(f"{label} hatasi: {resp.get('retCode')} {resp.get('retMsg')}")
        return resp

    def _retry(self, fn, max_tries: int = 3, **kwargs):
        last_exc = None
        for i in range(max_tries):
            try:
                return fn(**kwargs)
            except Exception as e:
                last_exc = e
                time.sleep(0.5 * (i + 1))
        raise last_exc

    # ------------------------------------------------------------------
    # Hesap
    # ------------------------------------------------------------------

    def fetch_balance_usdt(self) -> float:
        """Unified hesabin USDT toplam ozkaynak degeri."""
        resp = self._retry(self.session.get_wallet_balance,
                           accountType=ACCOUNT_TYPE, coin="USDT")
        self._check(resp, "get_wallet_balance")
        lst = resp.get("result", {}).get("list", [])
        if not lst:
            return 0.0
        acct = lst[0]
        for key in ("totalEquity", "totalWalletBalance", "totalAvailableBalance"):
            v = acct.get(key)
            if v not in (None, ""):
                try:
                    fv = float(v)
                    if fv > 0:
                        return fv
                except (TypeError, ValueError):
                    pass
        for coin in acct.get("coin", []):
            if coin.get("coin") == "USDT":
                for kk in ("equity", "walletBalance", "availableToWithdraw"):
                    vv = coin.get(kk)
                    if vv not in (None, ""):
                        try:
                            return float(vv)
                        except (TypeError, ValueError):
                            pass
        return 0.0

    # ------------------------------------------------------------------
    # Instrument
    # ------------------------------------------------------------------

    def get_instrument_info(self, symbol: str) -> dict:
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
        resp = self._retry(self.session.get_instruments_info,
                           category=CATEGORY, symbol=symbol)
        self._check(resp, "get_instruments_info")
        items = resp.get("result", {}).get("list", [])
        if not items:
            raise RuntimeError(f"{symbol} icin instrument bilgisi yok")
        it = items[0]
        pf = it.get("priceFilter", {})
        lf = it.get("lotSizeFilter", {})
        lev = it.get("leverageFilter", {})
        info = {
            "symbol":        symbol,
            "tick_size":     float(pf.get("tickSize", "0.0001")),
            "qty_step":      float(lf.get("qtyStep", "0.001")),
            "min_order_qty": float(lf.get("minOrderQty", "0.001")),
            "max_leverage":  float(lev.get("maxLeverage", "1")),
        }
        self._instrument_cache[symbol] = info
        return info

    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self._retry(self.session.set_leverage,
                        category=CATEGORY, symbol=symbol,
                        buyLeverage=str(leverage), sellLeverage=str(leverage))
        except Exception as e:
            if "not modified" in str(e).lower():
                return
            raise

    def switch_isolated(self, symbol: str, leverage: int) -> None:
        try:
            self._retry(self.session.switch_margin_mode,
                        category=CATEGORY, symbol=symbol, tradeMode=1,
                        buyLeverage=str(leverage), sellLeverage=str(leverage))
        except Exception as e:
            msg = str(e).lower()
            if "not modified" in msg or "already" in msg or "110026" in msg:
                return
            # Bazi hesaplarda zaten isolated - sessiz gec
            return

    # ------------------------------------------------------------------
    # Fiyat & klines
    # ------------------------------------------------------------------

    def fetch_kline(self, symbol: str, interval: str, limit: int = 200) -> List[List]:
        """En yeni mum basta doner. Cagiran taraf siralayabilir."""
        resp = self._retry(self.session.get_kline,
                           category=CATEGORY, symbol=symbol,
                           interval=interval, limit=limit)
        self._check(resp, "get_kline")
        return resp.get("result", {}).get("list", [])

    def fetch_last_price(self, symbol: str) -> float:
        resp = self._retry(self.session.get_tickers,
                           category=CATEGORY, symbol=symbol)
        self._check(resp, "get_tickers")
        items = resp.get("result", {}).get("list", [])
        if not items:
            raise RuntimeError(f"{symbol} ticker bulunamadi")
        return float(items[0]["lastPrice"])

    # ------------------------------------------------------------------
    # Pozisyon
    # ------------------------------------------------------------------

    def fetch_positions(self) -> List[dict]:
        resp = self._retry(self.session.get_positions,
                           category=CATEGORY, settleCoin="USDT")
        self._check(resp, "get_positions")
        items = resp.get("result", {}).get("list", [])
        return [it for it in items if float(it.get("size") or 0) > 0]

    # ------------------------------------------------------------------
    # Emir
    # ------------------------------------------------------------------

    def place_limit_post_only(
        self,
        symbol: str,
        side: str,          # "Buy" / "Sell"
        qty: str,
        price: str,
        reduce_only: bool = False,
    ) -> dict:
        params = dict(
            category=CATEGORY,
            symbol=symbol,
            side=side,
            orderType="Limit",
            qty=qty,
            price=price,
            timeInForce="PostOnly",
            reduceOnly=reduce_only,
        )
        resp = self._retry(self.session.place_order, **params)
        self._check(resp, f"place_limit_post_only {symbol} {side}")
        return resp.get("result", {})

    def place_market_order(
        self,
        symbol: str,
        side: str,          # "Buy" / "Sell"
        qty: str,
        reduce_only: bool = True,
    ) -> dict:
        """
        Market emir - SADECE cikis fallback'i icin kullanilir.
        Giriste asla kullanilmaz.
        """
        params = dict(
            category=CATEGORY,
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=qty,
            reduceOnly=reduce_only,
        )
        resp = self._retry(self.session.place_order, **params)
        self._check(resp, f"place_market_order {symbol} {side}")
        return resp.get("result", {})

    def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            self._retry(self.session.cancel_order,
                        category=CATEGORY, symbol=symbol, orderId=order_id)
        except Exception:
            pass  # zaten kapali / dolmus olabilir

    def get_order(self, symbol: str, order_id: str) -> Optional[dict]:
        # Once acik emirlerde ara
        try:
            resp = self._retry(self.session.get_open_orders,
                               category=CATEGORY, symbol=symbol, orderId=order_id)
            self._check(resp, "get_open_orders")
            lst = resp.get("result", {}).get("list", [])
            if lst:
                return lst[0]
        except Exception:
            pass
        # Sonra history'de ara
        try:
            resp = self._retry(self.session.get_order_history,
                               category=CATEGORY, symbol=symbol, orderId=order_id)
            self._check(resp, "get_order_history")
            lst = resp.get("result", {}).get("list", [])
            if lst:
                return lst[0]
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Stop Loss
    # ------------------------------------------------------------------

    def set_position_stop_loss(self, symbol: str, sl_price: str) -> None:
        """Mevcut pozisyona borsa-tarafli SL yerlestir."""
        try:
            self._retry(self.session.set_trading_stop,
                        category=CATEGORY,
                        symbol=symbol,
                        stopLoss=sl_price,
                        slTriggerBy="LastPrice",
                        positionIdx=0)
        except Exception as e:
            if "not modified" in str(e).lower():
                return
            raise
