"""
ATR TUNNEL Bot - Bybit V5 API Wrapper
Tüm Bybit API çağrılarını burada toplar.
"""
import logging
from decimal import Decimal
from typing import Optional

import pandas as pd
from pybit.unified_trading import HTTP

import config

logger = logging.getLogger(__name__)


class BybitClient:
    """Bybit V5 Unified Trading API client."""

    def __init__(self):
        api_key, api_secret = config.get_api_credentials()
        if not api_key or not api_secret:
            raise ValueError(
                "Bybit API key/secret yok! Environment variable'ları kontrol et."
            )
        self.client = HTTP(
            testnet=config.BYBIT_TESTNET,
            api_key=api_key,
            api_secret=api_secret,
            recv_window=5000,
        )
        self._instrument_cache: dict[str, dict] = {}

    # ============================================================
    # HESAP / BAKİYE
    # ============================================================

    def get_balance(self) -> float:
        """Unified hesabında USDT bakiyesi."""
        try:
            result = self.client.get_wallet_balance(
                accountType="UNIFIED", coin="USDT"
            )
            coins = result["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == "USDT":
                    val = c.get("walletBalance") or c.get("availableToWithdraw") or "0"
                    return float(val) if val else 0.0
            return 0.0
        except Exception as e:
            logger.error(f"Bakiye okuma hatası: {e}")
            return 0.0

    # ============================================================
    # PİYASA VERİSİ
    # ============================================================

    def get_kline(self, symbol: str, interval: str,
                  limit: int = 200) -> pd.DataFrame:
        """Kline verisini DataFrame olarak döndür.

        En eski mum başta, en yeni mum sonda olacak şekilde sıralanmış.
        Sütunlar: timestamp, open, high, low, close, volume, turnover
        """
        result = self.client.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
        rows = result["result"]["list"]
        # Bybit V5 descending döndürür (yeni başta), reverse et
        rows = list(reversed(rows))
        df = pd.DataFrame(rows, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        # Numeric'e çevir
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
        return df

    def get_all_tickers(self, symbols: list[str]) -> dict[str, float]:
        """Tüm sembollerin son fiyatını tek API çağrısıyla al."""
        try:
            result = self.client.get_tickers(category="linear")
            tickers = {}
            symbol_set = set(symbols)
            for t in result["result"]["list"]:
                if t["symbol"] in symbol_set:
                    tickers[t["symbol"]] = float(t["lastPrice"])
            return tickers
        except Exception as e:
            logger.error(f"Tickers hatası: {e}")
            return {}

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Tek sembolün son fiyatı."""
        try:
            result = self.client.get_tickers(
                category="linear", symbol=symbol
            )
            return float(result["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"{symbol} fiyat hatası: {e}")
            return None

    # ============================================================
    # ENSTRÜMAN BİLGİSİ (tick size, qty step)
    # ============================================================

    def get_instrument_info(self, symbol: str) -> dict:
        """Tick size, qty step gibi bilgileri al ve cache'le."""
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]
        try:
            result = self.client.get_instruments_info(
                category="linear", symbol=symbol
            )
            info = result["result"]["list"][0]
            self._instrument_cache[symbol] = info
            return info
        except Exception as e:
            logger.error(f"{symbol} instrument info hatası: {e}")
            return {}

    def get_tick_size(self, symbol: str) -> float:
        info = self.get_instrument_info(symbol)
        return float(info.get("priceFilter", {}).get("tickSize", "0.01"))

    def get_qty_step(self, symbol: str) -> float:
        info = self.get_instrument_info(symbol)
        return float(info.get("lotSizeFilter", {}).get("qtyStep", "0.001"))

    def get_min_qty(self, symbol: str) -> float:
        info = self.get_instrument_info(symbol)
        return float(info.get("lotSizeFilter", {}).get("minOrderQty", "0"))

    def round_price(self, symbol: str, price: float) -> float:
        """Fiyatı tick size'a göre yuvarla (aşağı)."""
        tick = self.get_tick_size(symbol)
        d_price = Decimal(str(price))
        d_tick = Decimal(str(tick))
        rounded = (d_price // d_tick) * d_tick
        return float(rounded)

    def format_price(self, symbol: str, price: float) -> str:
        """Fiyatı Decimal ile string'e çevir — float precision hatası olmadan."""
        tick = self.get_tick_size(symbol)
        d_price = Decimal(str(price))
        d_tick = Decimal(str(tick))
        rounded = (d_price // d_tick) * d_tick
        return format(rounded, 'f')

    def round_qty(self, symbol: str, qty: float) -> float:
        """Quantity'yi qty step'e göre yuvarla (aşağı)."""
        step = self.get_qty_step(symbol)
        d_qty = Decimal(str(qty))
        d_step = Decimal(str(step))
        rounded = (d_qty // d_step) * d_step
        return float(rounded)

    def format_qty(self, symbol: str, qty: float) -> str:
        """Quantity'yi Decimal ile string'e çevir — float precision hatası olmadan."""
        step = self.get_qty_step(symbol)
        d_qty = Decimal(str(qty))
        d_step = Decimal(str(step))
        rounded = (d_qty // d_step) * d_step
        return format(rounded, 'f')

    # ============================================================
    # KALDIRAÇ / MARJİN
    # ============================================================

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        try:
            self.client.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            return True
        except Exception as e:
            msg = str(e).lower()
            # "leverage not modified" gibi hata zaten leverage set demek
            if "not modified" in msg or "110043" in msg:
                return True
            logger.warning(f"{symbol} leverage hatası: {e}")
            return False

    def set_isolated_margin(self, symbol: str, leverage: int) -> bool:
        try:
            self.client.switch_margin_mode(
                category="linear",
                symbol=symbol,
                tradeMode=1,  # 1 = isolated, 0 = cross
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            return True
        except Exception as e:
            msg = str(e).lower()
            # 110026: zaten isolated; 100028: Unified hesapta desteklenmiyor (normal)
            if "not modified" in msg or "110026" in msg or "100028" in str(e):
                return True
            logger.warning(f"{symbol} isolated mode hatası: {e}")
            return False

    # ============================================================
    # EMİR İŞLEMLERİ
    # ============================================================

    def place_limit_postonly(self, symbol: str, side: str, qty: float,
                             price: float, reduce_only: bool = False) -> Optional[str]:
        """Limit post-only emir gönder.

        Args:
            side: "Buy" veya "Sell"
            reduce_only: True ise pozisyon kapatma için
        Returns:
            Order ID veya None
        """
        try:
            qty_str = self.format_qty(symbol, qty)
            price_str = self.format_price(symbol, price)
            params = {
                "category": "linear",
                "symbol": symbol,
                "side": side,
                "orderType": "Limit",
                "qty": qty_str,
                "price": price_str,
                "timeInForce": "PostOnly",
                "positionIdx": 0,  # one-way mode
            }
            if reduce_only:
                params["reduceOnly"] = True
            result = self.client.place_order(**params)
            return result["result"].get("orderId")
        except Exception as e:
            logger.warning(f"{symbol} limit emir hatası: {e}")
            return None

    def place_market_order(self, symbol: str, side: str, qty: float,
                          reduce_only: bool = False) -> Optional[str]:
        """Market emir gönder."""
        try:
            qty_str = self.format_qty(symbol, qty)
            params = {
                "category": "linear",
                "symbol": symbol,
                "side": side,
                "orderType": "Market",
                "qty": qty_str,
                "positionIdx": 0,
            }
            if reduce_only:
                params["reduceOnly"] = True
            result = self.client.place_order(**params)
            return result["result"].get("orderId")
        except Exception as e:
            logger.warning(f"{symbol} market emir hatası: {e}")
            return None

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            self.client.cancel_order(
                category="linear",
                symbol=symbol,
                orderId=order_id,
            )
            return True
        except Exception as e:
            # "order not exists" zaten iptal olmuş demek
            if "not exists" in str(e).lower() or "30032" in str(e):
                return True
            logger.warning(f"{symbol} iptal hatası: {e}")
            return False

    def is_order_filled(self, symbol: str, order_id: str) -> tuple[bool, Optional[dict]]:
        """Emir dolmuş mu? (filled, order_info) döndürür."""
        try:
            # Önce açık emirlerde ara
            result = self.client.get_open_orders(
                category="linear",
                symbol=symbol,
                orderId=order_id,
            )
            if result["result"]["list"]:
                # Açıkta - henüz dolmamış
                return False, result["result"]["list"][0]
            # Açıkta yok, history'e bak
            result = self.client.get_order_history(
                category="linear",
                symbol=symbol,
                orderId=order_id,
            )
            if result["result"]["list"]:
                order = result["result"]["list"][0]
                status = order.get("orderStatus", "")
                is_filled = status == "Filled"
                return is_filled, order
            return False, None
        except Exception as e:
            logger.error(f"{symbol} emir durumu hatası: {e}")
            return False, None

    # ============================================================
    # POZİSYON
    # ============================================================

    def get_position(self, symbol: str) -> Optional[dict]:
        """Açık pozisyon bilgisi."""
        try:
            result = self.client.get_positions(
                category="linear", symbol=symbol
            )
            if result["result"]["list"]:
                pos = result["result"]["list"][0]
                if float(pos.get("size", "0")) > 0:
                    return pos
            return None
        except Exception as e:
            logger.error(f"{symbol} pozisyon okuma hatası: {e}")
            return None

    def set_stop_loss(self, symbol: str, sl_price: float) -> bool:
        """Pozisyon seviyesinde SL set et."""
        try:
            price_str = self.format_price(symbol, sl_price)
            self.client.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=price_str,
                slTriggerBy="LastPrice",
                tpslMode="Full",
                slOrderType="Market",
                positionIdx=0,
            )
            return True
        except Exception as e:
            logger.error(f"{symbol} SL set hatası: {e}")
            return False

    # ============================================================
    # SAĞLIK KONTROLÜ
    # ============================================================

    def ping(self) -> bool:
        """API erişimini test et."""
        try:
            self.client.get_server_time()
            return True
        except Exception:
            return False
