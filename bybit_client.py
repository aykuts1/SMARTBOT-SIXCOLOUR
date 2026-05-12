"""
Bybit v5 (Unified Trading) API wrapper - pybit kütüphanesi üzerine.
Tüm API çağrıları retry mantığı ile sarmalanır.
"""
from __future__ import annotations

import logging
import math
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Dict, List, Optional, Tuple

import pandas as pd
from pybit.unified_trading import HTTP

import config

logger = logging.getLogger(__name__)


class BybitClient:
    """Bybit v5 unified trading wrapper."""

    CATEGORY = "linear"          # USDT perpetual
    ACCOUNT_TYPE = "UNIFIED"     # Unified account

    def __init__(self) -> None:
        self.session = HTTP(
            testnet=config.BYBIT_TESTNET,
            api_key=config.BYBIT_API_KEY,
            api_secret=config.BYBIT_API_SECRET,
            timeout=config.HTTP_TIMEOUT,
            recv_window=10000,
        )
        # Sembol bilgilerini cache'le (qty_step, price_tick, min_qty)
        self._instrument_cache: Dict[str, Dict[str, float]] = {}

    # ============= GENEL RETRY HELPER =============
    def _retry(self, fn, *args, **kwargs):
        """API çağrısını retry ile sarmala."""
        last_err: Optional[Exception] = None
        for attempt in range(1, config.RETRY_ATTEMPTS + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_err = e
                logger.warning(
                    f"API call failed (attempt {attempt}/{config.RETRY_ATTEMPTS}): {e}"
                )
                if attempt < config.RETRY_ATTEMPTS:
                    time.sleep(config.RETRY_DELAY)
        # Tüm denemeler başarısız
        raise last_err if last_err else RuntimeError("Unknown API error")

    @staticmethod
    def _check_ret(resp: dict, action: str) -> dict:
        """Bybit response'unun retCode'unu kontrol et."""
        if not isinstance(resp, dict):
            raise RuntimeError(f"{action}: invalid response type")
        ret_code = resp.get("retCode")
        if ret_code != 0:
            raise RuntimeError(
                f"{action} failed: retCode={ret_code}, "
                f"retMsg={resp.get('retMsg')}, resp={resp}"
            )
        return resp

    # ============= INSTRUMENT INFO =============
    def fetch_instrument_info(self, symbol: str) -> Dict[str, float]:
        """qty_step, price_tick, min_qty bilgilerini al ve cache'le."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        resp = self._retry(
            self.session.get_instruments_info,
            category=self.CATEGORY,
            symbol=symbol,
        )
        self._check_ret(resp, f"get_instruments_info({symbol})")

        items = resp.get("result", {}).get("list", [])
        if not items:
            raise RuntimeError(f"Instrument bulunamadı: {symbol}")

        item = items[0]
        lot_filter = item.get("lotSizeFilter", {})
        price_filter = item.get("priceFilter", {})

        info = {
            "qty_step": float(lot_filter.get("qtyStep", "0.001")),
            "min_qty": float(lot_filter.get("minOrderQty", "0.001")),
            "price_tick": float(price_filter.get("tickSize", "0.01")),
            "max_qty": float(lot_filter.get("maxOrderQty", "9999999")),
        }
        self._instrument_cache[symbol] = info
        return info

    def round_qty(self, symbol: str, qty: float) -> float:
        """Miktarı qty_step'e yuvarla (aşağı)."""
        info = self.fetch_instrument_info(symbol)
        step = Decimal(str(info["qty_step"]))
        q = Decimal(str(qty))
        rounded = (q // step) * step
        return float(rounded)

    def round_price(self, symbol: str, price: float, round_up: bool = False) -> float:
        """Fiyatı price_tick'e yuvarla."""
        info = self.fetch_instrument_info(symbol)
        tick = Decimal(str(info["price_tick"]))
        p = Decimal(str(price))
        if round_up:
            rounded = ((p + tick - Decimal("0.0000000001")) // tick) * tick
        else:
            rounded = (p // tick) * tick
        return float(rounded)

    def qty_to_str(self, symbol: str, qty: float) -> str:
        """Miktarı API için string'e çevir (gereksiz sıfırlar olmadan)."""
        info = self.fetch_instrument_info(symbol)
        step = Decimal(str(info["qty_step"]))
        # qty_step'in decimal digit sayısını bul
        exp = step.as_tuple().exponent
        decimals = max(0, -exp) if isinstance(exp, int) else 0
        return f"{qty:.{decimals}f}"

    def price_to_str(self, symbol: str, price: float) -> str:
        """Fiyatı API için string'e çevir."""
        info = self.fetch_instrument_info(symbol)
        tick = Decimal(str(info["price_tick"]))
        exp = tick.as_tuple().exponent
        decimals = max(0, -exp) if isinstance(exp, int) else 0
        return f"{price:.{decimals}f}"

    # ============= KLINE =============
    def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 250,
    ) -> pd.DataFrame:
        """
        Kline (mum) verisi çek. Sadece KAPANAN mumları döndürür.
        Sütunlar: open_time, open, high, low, close, volume.
        Sıralama: en eskiden en yeniye.
        """
        resp = self._retry(
            self.session.get_kline,
            category=self.CATEGORY,
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
        self._check_ret(resp, f"get_kline({symbol},{interval})")

        rows = resp.get("result", {}).get("list", [])
        if not rows:
            raise RuntimeError(f"Kline boş: {symbol} {interval}")

        # Bybit en yeniyi başta verir, ters çevir
        rows = list(reversed(rows))
        df = pd.DataFrame(
            rows,
            columns=["open_time", "open", "high", "low", "close", "volume", "turnover"],
        )
        df["open_time"] = pd.to_numeric(df["open_time"])
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c])

        # SON mum şu anda gelişen (kapanmamış) mum olabilir.
        # Mum kapanış zamanı = open_time + interval. interval_ms'i belirleyelim:
        interval_ms = self._interval_to_ms(interval)
        now_ms = int(time.time() * 1000)
        # Son mumun kapanış anı geçmediyse onu at
        last_open_time = int(df["open_time"].iloc[-1])
        if last_open_time + interval_ms > now_ms:
            df = df.iloc[:-1].reset_index(drop=True)

        return df

    @staticmethod
    def _interval_to_ms(interval: str) -> int:
        """Bybit interval string'ini millisaniyeye çevir."""
        if interval.isdigit():
            return int(interval) * 60 * 1000
        # D, W, M
        mapping = {"D": 86400_000, "W": 7 * 86400_000, "M": 30 * 86400_000}
        return mapping.get(interval.upper(), 60_000)

    # ============= BAKİYE =============
    def fetch_usdt_balance(self) -> float:
        """Unified hesabın toplam equity değerini döndür (açık pozisyonlar dahil)."""
        resp = self._retry(
            self.session.get_wallet_balance,
            accountType=self.ACCOUNT_TYPE,
            coin="USDT",
        )
        self._check_ret(resp, "get_wallet_balance")

        lst = resp.get("result", {}).get("list", [])
        if not lst:
            return 0.0

        account = lst[0]

        # totalEquity: unrealized PnL dahil gerçek hesap değeri
        for field in ("totalEquity", "totalWalletBalance", "totalAvailableBalance"):
            val = account.get(field)
            if val not in (None, ""):
                try:
                    result = float(val)
                    if result > 0:
                        return result
                except (TypeError, ValueError):
                    continue

        # Fallback: coin listesindeki USDT walletBalance
        for coin_info in account.get("coin", []):
            if coin_info.get("coin") == "USDT":
                val = coin_info.get("walletBalance") or "0"
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    # ============= KALDIRAÇ =============
    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Sembol için kaldıracı set et. Zaten ayarlıysa hata yutulur."""
        try:
            resp = self.session.set_leverage(
                category=self.CATEGORY,
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            if isinstance(resp, dict):
                ret_code = resp.get("retCode")
                # 110043 = leverage not modified (zaten aynı)
                if ret_code not in (0, 110043):
                    logger.warning(
                        f"set_leverage {symbol}: retCode={ret_code}, "
                        f"msg={resp.get('retMsg')}"
                    )
        except Exception as e:
            msg = str(e).lower()
            if "leverage not modified" in msg or "110043" in msg:
                return
            logger.warning(f"set_leverage {symbol} exception: {e}")

    # ============= ANLIK FİYAT =============
    def fetch_last_price(self, symbol: str) -> float:
        """Sembolün son işlem fiyatını döndür."""
        resp = self._retry(
            self.session.get_tickers,
            category=self.CATEGORY,
            symbol=symbol,
        )
        self._check_ret(resp, f"get_tickers({symbol})")
        lst = resp.get("result", {}).get("list", [])
        if not lst:
            raise RuntimeError(f"Tickers boş: {symbol}")
        return float(lst[0]["lastPrice"])

    # ============= POZİSYON =============
    def fetch_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Açık pozisyonları döndür. Symbol verilirse o sembol, verilmezse tümü."""
        params = {"category": self.CATEGORY}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = "USDT"

        resp = self._retry(self.session.get_positions, **params)
        self._check_ret(resp, f"get_positions({symbol})")

        positions = resp.get("result", {}).get("list", [])
        # Sadece size > 0 olanlar
        open_positions = []
        for p in positions:
            size = p.get("size", "0")
            try:
                if float(size) > 0:
                    open_positions.append(p)
            except (TypeError, ValueError):
                continue
        return open_positions

    def fetch_position(self, symbol: str) -> Optional[Dict]:
        """Belirli sembolün açık pozisyonunu döndür, yoksa None."""
        positions = self.fetch_positions(symbol)
        return positions[0] if positions else None

    # ============= EMİR =============
    def place_limit_order(
        self,
        symbol: str,
        side: str,            # "Buy" / "Sell"
        qty: float,
        price: float,
        reduce_only: bool = False,
        time_in_force: str = "GTC",
    ) -> Dict:
        """Limit emir gönder."""
        qty_str = self.qty_to_str(symbol, qty)
        price_str = self.price_to_str(symbol, price)

        params = {
            "category": self.CATEGORY,
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": qty_str,
            "price": price_str,
            "timeInForce": time_in_force,
            "reduceOnly": reduce_only,
        }
        resp = self._retry(self.session.place_order, **params)
        self._check_ret(resp, f"place_order({symbol},{side},{qty_str}@{price_str})")
        return resp.get("result", {})

    def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        reduce_only: bool = False,
    ) -> Dict:
        """Market emir (acil kapanış için fallback)."""
        qty_str = self.qty_to_str(symbol, qty)
        params = {
            "category": self.CATEGORY,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty_str,
            "reduceOnly": reduce_only,
        }
        resp = self._retry(self.session.place_order, **params)
        self._check_ret(resp, f"market_order({symbol},{side},{qty_str})")
        return resp.get("result", {})

    def cancel_all_orders(self, symbol: str) -> None:
        """Sembol için tüm açık emirleri iptal et."""
        try:
            resp = self.session.cancel_all_orders(
                category=self.CATEGORY,
                symbol=symbol,
            )
            if isinstance(resp, dict) and resp.get("retCode") not in (0, 110001):
                logger.warning(f"cancel_all_orders {symbol}: {resp.get('retMsg')}")
        except Exception as e:
            logger.warning(f"cancel_all_orders {symbol}: {e}")

    # ============= STOP LOSS =============
    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        position_idx: int = 0,
    ) -> None:
        """
        Pozisyon seviyesinde stop loss / take profit ayarla.
        position_idx: 0 = one-way mode, 1=Buy/long hedge, 2=Sell/short hedge
        """
        params = {
            "category": self.CATEGORY,
            "symbol": symbol,
            "positionIdx": position_idx,
            "tpslMode": "Full",
        }
        if stop_loss is not None:
            params["stopLoss"] = self.price_to_str(symbol, stop_loss)
            params["slTriggerBy"] = "LastPrice"
            params["slOrderType"] = "Market"
        if take_profit is not None:
            params["takeProfit"] = self.price_to_str(symbol, take_profit)
            params["tpTriggerBy"] = "LastPrice"
            params["tpOrderType"] = "Market"

        try:
            resp = self.session.set_trading_stop(**params)
            if isinstance(resp, dict):
                ret_code = resp.get("retCode")
                # 34040 = not modified, 10001 sometimes for already set
                if ret_code not in (0, 34040):
                    logger.warning(
                        f"set_trading_stop {symbol}: retCode={ret_code}, "
                        f"msg={resp.get('retMsg')}"
                    )
        except Exception as e:
            msg = str(e).lower()
            if "not modified" in msg or "34040" in msg:
                return
            raise

    # ============= KAPANAN POZİSYON PNL =============
    def fetch_closed_pnl(self, symbol: str, limit: int = 10) -> List[Dict]:
        """Son kapanan pozisyonların PnL'ini al."""
        resp = self._retry(
            self.session.get_closed_pnl,
            category=self.CATEGORY,
            symbol=symbol,
            limit=limit,
        )
        self._check_ret(resp, f"get_closed_pnl({symbol})")
        return resp.get("result", {}).get("list", [])

    def fetch_latest_closed_pnl(self, symbol: str) -> Optional[Dict]:
        """En son kapanan pozisyonun PnL kaydını döndür."""
        lst = self.fetch_closed_pnl(symbol, limit=1)
        return lst[0] if lst else None
