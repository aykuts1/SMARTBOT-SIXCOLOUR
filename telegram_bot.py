"""
Telegram bildirim wrapper'ı - requests ile direkt HTTP.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram bot mesaj gönderici."""

    BASE_URL = "https://api.telegram.org"

    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.session = requests.Session()
        self.api_url = f"{self.BASE_URL}/bot{token}/sendMessage"

    def send(self, text: str, parse_mode: str = "HTML", silent: bool = False) -> bool:
        """Mesaj gönder. Hata olursa logla, exception fırlatma."""
        if not self.token or not self.chat_id:
            logger.warning("Telegram token/chat_id boş, mesaj atlanıyor")
            return False

        # Mesaj çok uzunsa böl (Telegram limit: 4096 karakter)
        if len(text) > 4000:
            parts = self._split_message(text, 4000)
            ok = True
            for p in parts:
                if not self._send_single(p, parse_mode, silent):
                    ok = False
            return ok

        return self._send_single(text, parse_mode, silent)

    def _send_single(self, text: str, parse_mode: str, silent: bool) -> bool:
        """Tek mesaj gönder, retry ile."""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "disable_notification": silent,
        }

        last_err: Optional[Exception] = None
        for attempt in range(1, config.RETRY_ATTEMPTS + 1):
            try:
                resp = self.session.post(
                    self.api_url, json=payload, timeout=config.HTTP_TIMEOUT
                )
                if resp.status_code == 200:
                    return True
                # HTML parse hatası ise parse_mode olmadan tekrar dene
                if resp.status_code == 400 and parse_mode:
                    logger.warning(f"Telegram parse hatası, plain text tekrar: {resp.text[:200]}")
                    payload2 = dict(payload)
                    payload2.pop("parse_mode", None)
                    resp2 = self.session.post(
                        self.api_url, json=payload2, timeout=config.HTTP_TIMEOUT
                    )
                    if resp2.status_code == 200:
                        return True
                logger.warning(
                    f"Telegram gönderim başarısız (deneme {attempt}): "
                    f"status={resp.status_code}, body={resp.text[:200]}"
                )
            except Exception as e:
                last_err = e
                logger.warning(f"Telegram exception (deneme {attempt}): {e}")
            time.sleep(config.RETRY_DELAY)

        if last_err:
            logger.error(f"Telegram gönderimi başarısız: {last_err}")
        return False

    @staticmethod
    def _split_message(text: str, max_len: int) -> list:
        """Mesajı satır sınırlarında böl."""
        if len(text) <= max_len:
            return [text]
        lines = text.split("\n")
        parts = []
        cur = []
        cur_len = 0
        for line in lines:
            line_len = len(line) + 1
            if cur_len + line_len > max_len and cur:
                parts.append("\n".join(cur))
                cur = [line]
                cur_len = line_len
            else:
                cur.append(line)
                cur_len += line_len
        if cur:
            parts.append("\n".join(cur))
        return parts


# ============= MESAJ ŞABLONLARI =============

def fmt_startup(balance: float, stake: float, leverage: int, symbol_count: int) -> str:
    return (
        "🚀 <b>Bot Başladı</b>\n"
        f"💵 Bakiye: <b>{balance:.2f} USDT</b>\n"
        f"📊 Stake (sabit): <b>{stake:.2f} USDT</b>\n"
        f"⚡ Kaldıraç: <b>{leverage}x</b>\n"
        f"🎯 Sembol sayısı: <b>{symbol_count}</b>\n"
        f"🛑 Stop: %1 + Chandelier Exit (1 ATR)"
    )


def fmt_entry(
    symbol: str, side: str, entry_price: float, qty: float,
    stop_price: float, ce_price: float, stake: float, leverage: int,
) -> str:
    arrow = "🟢 LONG" if side == "long" else "🔴 SHORT"
    return (
        f"{arrow} <b>{symbol}</b>\n"
        f"📈 Giriş: <b>{entry_price:.6f}</b>\n"
        f"📦 Miktar: <b>{qty}</b>\n"
        f"🛑 Stop (%1): <b>{stop_price:.6f}</b>\n"
        f"🎯 CE başlangıç: <b>{ce_price:.6f}</b>\n"
        f"💰 Stake: <b>{stake:.2f} USDT</b> × <b>{leverage}x</b>"
    )


def fmt_exit(
    symbol: str, side: str, exit_price: float,
    reason: str, pnl_usdt: float, pnl_pct: float,
) -> str:
    arrow = "🟢 LONG" if side == "long" else "🔴 SHORT"
    emoji = "✅" if pnl_usdt >= 0 else "❌"
    return (
        f"{emoji} <b>{symbol}</b> {arrow} KAPANDI\n"
        f"🚪 Çıkış: <b>{exit_price:.6f}</b>\n"
        f"📋 Sebep: <b>{reason}</b>\n"
        f"💸 PnL: <b>{pnl_usdt:+.2f} USDT</b> ({pnl_pct:+.2f}%)"
    )


def fmt_max_positions(symbol: str, side: str) -> str:
    return (
        f"⚠️ <b>{symbol}</b> {side.upper()} sinyali geldi ama "
        f"5 pozisyon dolu, atlandı."
    )


def fmt_duplicate(symbol: str, side: str) -> str:
    return (
        f"⚠️ <b>{symbol}</b> {side.upper()} sinyali geldi ama "
        f"bu coinde zaten açık pozisyon var, atlandı."
    )


def fmt_filter_reject(symbol: str, side: str, reason: str) -> str:
    arrow = "🟢" if side == "long" else "🔴"
    return (
        f"{arrow} <b>{symbol}</b> {side.upper()} crossover var "
        f"ama filtreye takıldı: <i>{reason}</i>"
    )


def fmt_error(context: str, error: str) -> str:
    # HTML escape için < > & değiştir
    safe_err = (
        str(error).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    safe_ctx = (
        str(context).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"🚨 <b>HATA</b> [{safe_ctx}]\n<code>{safe_err[:500]}</code>"


def _categorize_rejection(reason: str) -> str:
    """Rejection reason'ı kategoriye çevir."""
    r = reason.lower()
    if "30dk ema" in r:
        return "30dk EMA"
    if "2h ema" in r:
        return "2H EMA"
    if "atr" in r:
        return "ATR filtresi"
    return "Diğer"


def fmt_scan_summary(
    scan_time: str,
    total_symbols: int,
    opened: list,            # list[(symbol, side)]
    filter_rejections: list, # list[(symbol, side, reason)]
    max_pos_skips: list,     # list[(symbol, side)]
    duplicate_skips: list,   # list[(symbol, side)]
    open_count: int,
    stake: float,
    leverage: int,
) -> str:
    lines = []
    lines.append("📊 <b>30dk Tarama Özeti</b>")
    lines.append(f"⏰ {scan_time} | {total_symbols} sembol tarandı")
    lines.append("")

    # Açılan pozisyonlar
    lines.append(f"✅ <b>Açılan pozisyon: {len(opened)}</b>")
    for sym, sd in opened:
        lines.append(f"{sym} {sd.upper()}")
    lines.append("")

    # Crossover gruplandırması
    counts = {}
    for _sym, _sd, reason in filter_rejections:
        cat = _categorize_rejection(reason)
        counts[cat] = counts.get(cat, 0) + 1

    lines.append(f"⚠️ <b>Crossover: {len(filter_rejections)}</b>")
    for cat in ("30dk EMA", "2H EMA", "ATR filtresi", "Diğer"):
        if counts.get(cat, 0) > 0:
            lines.append(f"{cat}: {counts[cat]}")
    lines.append("")

    lines.append(f"🚫 5 pozisyon dolu: {len(max_pos_skips)}")
    lines.append(f"📌 Zaten açık: {len(duplicate_skips)}")
    lines.append("")
    lines.append(f"📈 Açık pozisyon: {open_count}/{config.MAX_POSITIONS}")
    lines.append(f"💰 Stake: {stake:.2f} USDT × {leverage}x")

    return "\n".join(lines)
