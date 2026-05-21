"""
notifier.py - Telegram bildirim sistemi.

16 bildirim tipi:
  Olaylar (12):
    1.  bot_started
    2.  bot_stopped
    3.  api_connection_error
    4.  api_key_invalid
    5.  render_restarted
    6.  slot_full
    7.  flag_opened
    8.  flag_deleted
    9.  trade_opened
    10. level_changed
    11. trade_closed
    12. entry_order_failed (limit emir dolmadi - sinyal atlandi)

  Raporlar (4):
    13. report_15min
    14. report_hourly
    15. report_8h
    16. report_daily
"""

import threading
from datetime import datetime
from typing import List, Optional

import requests


class Notifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------
    # Dusuk seviye gonderim
    # ---------------------------------------------------------------------

    def _send(self, text: str) -> None:
        if not self.token or not self.chat_id:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        with self._lock:
            try:
                requests.post(url, json=payload, timeout=10)
            except Exception:
                # Telegram hatasi botu durdurmaz
                pass

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    @staticmethod
    def _fmt_money(v: float) -> str:
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @staticmethod
    def _fmt_pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.2f}%"

    @staticmethod
    def _fmt_atr(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.2f} ATR"

    # ---------------------------------------------------------------------
    # 1. Bot baslatildi
    # ---------------------------------------------------------------------

    def bot_started(self, balance: float, stake: float, cfg) -> None:
        lines = [
            "🟢 <b>BOT BASLATILDI</b>",
            f"Tarih: {self._now()}",
            f"Bakiye: {self._fmt_money(balance)} USDT",
            f"Stake: {self._fmt_money(stake)} USDT ({cfg.stake_percent:.0f}%)",
            "",
            "<b>Aktif Parametreler:</b>",
            f"• Max Islem: {cfg.max_open_trades}",
            f"• Kaldirac: {cfg.leverage}x",
            f"• Borsa SL: {cfg.sl_percent}%",
            f"• Zaman Dilimi: {cfg.timeframe}m",
            f"• EMA: {cfg.ema_period}",
            f"• ATR: {cfg.atr_period}",
            f"• Band Carpani: {cfg.band_multiplier}",
            f"• Tampon Carpani: {cfg.buffer_multiplier}",
            f"• CE1: {cfg.ce1_atr} ATR / Takip: {cfg.ce1_trail} ATR",
            f"• CE2: {cfg.ce2_atr} ATR / Takip: {cfg.ce2_trail} ATR",
            f"• Winrate: {cfg.winrate_atr} ATR / Takip: {cfg.winrate_trail} ATR",
            f"• Coin Sayisi: {len(cfg.coins)}",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 2. Bot durduruldu
    # ---------------------------------------------------------------------

    def bot_stopped(self, reason: str = "Manuel kapatma") -> None:
        lines = [
            "🔴 <b>BOT DURDURULDU</b>",
            f"Tarih: {self._now()}",
            f"Sebep: {reason}",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 3. Bybit API baglanti hatasi
    # ---------------------------------------------------------------------

    def api_connection_error(self, detail: str) -> None:
        lines = [
            "⚠️ <b>BYBIT API BAGLANTI HATASI</b>",
            f"Tarih: {self._now()}",
            f"Detay: {detail}",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 4. API anahtari gecersiz
    # ---------------------------------------------------------------------

    def api_key_invalid(self) -> None:
        lines = [
            "❌ <b>BYBIT API ANAHTARI GECERSIZ</b>",
            f"Tarih: {self._now()}",
            "API key ve secret'i kontrol edin.",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 5. Render yeniden baslatildi
    # ---------------------------------------------------------------------

    def render_restarted(self) -> None:
        lines = [
            "🔄 <b>RENDER YENIDEN BASLATILDI</b>",
            f"Tarih: {self._now()}",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 6. Slot dolu - sinyal atlandi
    # ---------------------------------------------------------------------

    def slot_full(self, symbol: str, side: str, used: int, max_slots: int) -> None:
        lines = [
            "🔕 <b>SLOT DOLU - SINYAL ATLANDI</b>",
            f"Tarih: {self._now()}",
            f"Coin: {symbol}",
            f"Yon: {side}",
            f"Acik islem: {used}/{max_slots}",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 7. Flag acildi
    # ---------------------------------------------------------------------

    def flag_opened(self, symbol: str, side: str, price: float) -> None:
        arrow = "📈" if side == "LONG" else "📉"
        lines = [
            f"{arrow} <b>FLAG ACILDI</b>",
            f"Tarih: {self._now()}",
            f"Coin: {symbol}",
            f"Yon: {side}",
            f"Anlik Fiyat: {price}",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 8. Flag silindi
    # ---------------------------------------------------------------------

    def flag_deleted(self, symbol: str, side: str, price: float) -> None:
        lines = [
            "🗑️ <b>FLAG SILINDI</b>",
            f"Tarih: {self._now()}",
            f"Coin: {symbol}",
            f"Yon: {side}",
            f"Anlik Fiyat: {price}",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 9. Islem acildi
    # ---------------------------------------------------------------------

    def trade_opened(
        self, symbol: str, side: str, entry: float,
        stake: float, notional: float, sl_price: float,
    ) -> None:
        arrow = "🟩" if side == "LONG" else "🟥"
        lines = [
            f"{arrow} <b>ISLEM ACILDI</b>",
            f"Tarih: {self._now()}",
            f"Coin: {symbol}",
            f"Yon: {side}",
            f"Giris Fiyati: {entry}",
            f"Stake: {self._fmt_money(stake)} USDT",
            f"Hacim: {self._fmt_money(notional)} USDT",
            f"SL Seviyesi: {sl_price}",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 10. Seviye gecisi
    # ---------------------------------------------------------------------

    def level_changed(
        self, symbol: str, side: str, level_label: str,
        price: float, pnl_usdt: float, pnl_pct: float,
    ) -> None:
        lines = [
            "🎯 <b>SEVIYE GECISI</b>",
            f"Tarih: {self._now()}",
            f"Coin: {symbol}",
            f"Yon: {side}",
            f"Yeni Seviye: {level_label}",
            f"Anlik Fiyat: {price}",
            f"K/Z: {self._fmt_money(pnl_usdt)} USDT  ({self._fmt_pct(pnl_pct)})",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 11. Islem kapandi
    # ---------------------------------------------------------------------

    def trade_closed(
        self, symbol: str, side: str, entry: float, exit_price: float,
        exit_type: str, pnl_usdt: float, pnl_pct: float, atr_profit: float,
        market_fallback: bool = False,
    ) -> None:
        emoji = "✅" if pnl_usdt >= 0 else "❌"
        suffix = " (market fallback)" if market_fallback else ""
        lines = [
            f"{emoji} <b>ISLEM KAPANDI</b>",
            f"Tarih: {self._now()}",
            f"Coin: {symbol}",
            f"Yon: {side}",
            f"Giris: {entry}",
            f"Cikis: {exit_price}",
            f"Cikis Tipi: {exit_type}{suffix}",
            f"K/Z: {self._fmt_money(pnl_usdt)} USDT  ({self._fmt_pct(pnl_pct)})  "
            f"[{self._fmt_atr(atr_profit)}]",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 12. Limit emir dolmadi - sinyal atlandi (giris)
    # ---------------------------------------------------------------------

    def entry_order_failed(self, symbol: str, side: str, attempts: int) -> None:
        lines = [
            "⏭️ <b>LIMIT EMIR DOLMADI - SINYAL ATLANDI</b>",
            f"Tarih: {self._now()}",
            f"Coin: {symbol}",
            f"Yon: {side}",
            f"{attempts} denemede emir dolmadi.",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 13. Stoploss tespit edildi (borsa kapatmasi)
    # ---------------------------------------------------------------------

    def stoploss_detected(
        self, symbol: str, side: str, entry: float,
        exit_price: float, pnl_usdt: float, pnl_pct: float,
    ) -> None:
        lines = [
            "🛑 <b>STOPLOSS TETIKLENDI</b>",
            f"Tarih: {self._now()}",
            f"Coin: {symbol}",
            f"Yon: {side}",
            f"Giris: {entry}",
            f"Tahmini Cikis: {exit_price}",
            f"K/Z: {self._fmt_money(pnl_usdt)} USDT  ({self._fmt_pct(pnl_pct)})",
        ]
        self._send("\n".join(lines))

    # ---------------------------------------------------------------------
    # 14-17. Raporlar (gelen formatli text gonderir)
    # ---------------------------------------------------------------------

    def report(self, text: str) -> None:
        self._send(text)
