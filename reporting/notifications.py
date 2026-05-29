"""Bildirim metin formatları.

Tüm metinler HTML parse_mode için hazırdır.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.trade_manager import Trade, LEVEL_NAMES


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_trade_open(t: Trade) -> str:
    arrow = "🟢 LONG" if t.side == "long" else "🔴 SHORT"
    return (
        f"<b>{arrow} {t.symbol}</b>\n"
        f"Giriş: <code>{t.levels.entry:.6f}</code>\n"
        f"Lose Exit: <code>{t.levels.lose_exit:.6f}</code>\n"
        f"Winrate: <code>{t.levels.winrate:.6f}</code>\n"
        f"Size: <code>{t.size}</code>  •  Stake: <code>{t.stake_usdt:.2f} USDT</code>\n"
        f"ID: <code>{t.trade_id}</code>"
    )


def fmt_level_up(t: Trade, old_level: int) -> str:
    arrow = "📈" if t.side == "long" else "📉"
    return (
        f"{arrow} <b>{t.symbol}</b> seviye: "
        f"{LEVEL_NAMES[old_level-1]} → <b>{LEVEL_NAMES[t.level-1]}</b>\n"
        f"ID: <code>{t.trade_id}</code>"
    )


def fmt_trade_close(t: Trade, exit_price: float, reason: str, pnl_usdt: float,
                    pnl_pct: float) -> str:
    sign = "✅" if pnl_usdt > 0 else "❌"
    return (
        f"{sign} <b>{t.symbol} kapandı</b> ({t.side.upper()})\n"
        f"Sebep: <code>{reason}</code>\n"
        f"Giriş: <code>{t.levels.entry:.6f}</code> → Çıkış: <code>{exit_price:.6f}</code>\n"
        f"PnL: <b>{pnl_usdt:+.2f} USDT</b> ({pnl_pct:+.2f}%)\n"
        f"Seviye: {LEVEL_NAMES[t.level-1]}  •  ID: <code>{t.trade_id}</code>"
    )


def fmt_flag_opened(symbol: str, side: str, trigger: float) -> str:
    icon = "🏳️ 🟢" if side == "long" else "🏳️ 🔴"
    return f"{icon} <b>{symbol}</b> {side.upper()} flag — tetik: <code>{trigger:.6f}</code>"


def fmt_error(msg: str) -> str:
    return f"⚠️ <b>HATA</b>\n<code>{msg}</code>"


def fmt_insufficient_balance(symbol: str, needed: float, have: float) -> str:
    return (
        f"💸 <b>Yetersiz bakiye</b> ({symbol})\n"
        f"Gerekli: <code>{needed:.2f}</code> | Mevcut: <code>{have:.2f}</code>"
    )


def fmt_slot_full(symbol: str, side: str) -> str:
    return f"🚫 Slot dolu — {symbol} {side} açılamadı"


def fmt_unknown_position(symbol: str, side: str, size: float) -> str:
    return (
        f"❓ <b>Bilinmeyen pozisyon</b>\n"
        f"{symbol} {side} size={size} — bot tarafından takip EDİLMİYOR"
    )
