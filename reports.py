"""
reports.py - 15 dakikalik, saatlik, 8 saatlik ve gunluk rapor formatlama.

Tum raporlar HTML formatinda dondurulur, notifier.report() ile gonderilir.
"""

from datetime import datetime
from typing import List

from position import Position, LEVEL_LABELS


def _now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def _fmt_money(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _fmt_position_line(pos: Position, current_price: float) -> str:
    """Acik pozisyonun tek satirlik durumu."""
    pnl_usdt = pos.profit_usdt(current_price)
    pnl_pct  = pos.profit_pct_leveraged(current_price)
    return (
        f"  • {pos.symbol} {pos.side}  Giris:{pos.entry_price}  "
        f"Anlik:{current_price}  Seviye:{LEVEL_LABELS[pos.level]}  "
        f"K/Z:{_fmt_money(pnl_usdt)} ({_fmt_pct(pnl_pct)})"
    )


def _fmt_closed_line(r: dict) -> str:
    sign = "+" if r["pnl_usdt"] >= 0 else ""
    return (
        f"  • {r['symbol']} {r['side']}  Giris:{r['entry']}  Cikis:{r['exit']}  "
        f"{r['exit_type']}  K/Z:{sign}{_fmt_money(r['pnl_usdt'])} "
        f"({_fmt_pct(r['pnl_pct'])})"
    )


# ---------------------------------------------------------------------------
# 15 dakikalik durum raporu
# ---------------------------------------------------------------------------

def build_report_15min(
    balance: float,
    positions: List[Position],
    prices: dict,    # symbol -> last price
    max_slots: int,
) -> str:
    lines = [
        "🕒 <b>15 DAKIKALIK DURUM RAPORU</b>",
        f"Tarih: {_now()}",
        f"Bakiye: {_fmt_money(balance)} USDT",
        f"Acik Islem: {len(positions)}/{max_slots}",
    ]
    if positions:
        lines.append("")
        lines.append("<b>Acik Pozisyonlar:</b>")
        for p in positions:
            cp = prices.get(p.symbol, p.entry_price)
            lines.append(_fmt_position_line(p, cp))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Saatlik rapor (son 1 saat)
# ---------------------------------------------------------------------------

def build_report_hourly(
    balance: float,
    positions: List[Position],
    prices: dict,
    closed: List[dict],   # son 1 saatte kapanan islemler
    max_slots: int,
) -> str:
    win  = sum(1 for r in closed if r["pnl_usdt"] >= 0)
    lose = sum(1 for r in closed if r["pnl_usdt"] <  0)
    net  = sum(r["pnl_usdt"] for r in closed)

    lines = [
        "📊 <b>SAATLIK RAPOR</b>",
        f"Tarih: {_now()}",
        f"Bakiye: {_fmt_money(balance)} USDT",
        f"Acik Islem: {len(positions)}/{max_slots}",
        "",
        "<b>Son 1 Saat:</b>",
        f"  Kapanan: {len(closed)}  |  Karli: {win}  |  Zararli: {lose}",
        f"  Net K/Z: {_fmt_money(net)} USDT",
    ]

    if closed:
        lines.append("")
        lines.append("<b>Kapanan Islemler:</b>")
        for r in closed:
            lines.append(_fmt_closed_line(r))

    if positions:
        lines.append("")
        lines.append("<b>Acik Pozisyonlar:</b>")
        for p in positions:
            cp = prices.get(p.symbol, p.entry_price)
            lines.append(_fmt_position_line(p, cp))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8 saatlik vardiya raporu
# ---------------------------------------------------------------------------

def build_report_8h(
    balance: float,
    positions: List[Position],
    prices: dict,
    closed: List[dict],
    max_slots: int,
) -> str:
    win  = sum(1 for r in closed if r["pnl_usdt"] >= 0)
    lose = sum(1 for r in closed if r["pnl_usdt"] <  0)
    net  = sum(r["pnl_usdt"] for r in closed)

    lines = [
        "🕗 <b>8 SAATLIK VARDIYA RAPORU</b>",
        f"Tarih: {_now()}",
        f"Bakiye: {_fmt_money(balance)} USDT",
        f"Acik Islem: {len(positions)}/{max_slots}",
        "",
        "<b>Son 8 Saat:</b>",
        f"  Kapanan: {len(closed)}  |  Karli: {win}  |  Zararli: {lose}",
        f"  Net K/Z: {_fmt_money(net)} USDT",
    ]

    if closed:
        lines.append("")
        lines.append("<b>Kapanan Islemler:</b>")
        for r in closed:
            lines.append(_fmt_closed_line(r))

    if positions:
        lines.append("")
        lines.append("<b>Acik Pozisyonlar:</b>")
        for p in positions:
            cp = prices.get(p.symbol, p.entry_price)
            lines.append(_fmt_position_line(p, cp))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 24 saatlik gunluk rapor
# ---------------------------------------------------------------------------

def build_report_daily(
    balance: float,
    positions: List[Position],
    prices: dict,
    closed: List[dict],
    max_slots: int,
) -> str:
    win  = sum(1 for r in closed if r["pnl_usdt"] >= 0)
    lose = sum(1 for r in closed if r["pnl_usdt"] <  0)
    net  = sum(r["pnl_usdt"] for r in closed)

    lines = [
        "📅 <b>24 SAATLIK GUNLUK RAPOR</b>",
        f"Tarih: {_now()}",
        f"Bakiye: {_fmt_money(balance)} USDT",
        f"Acik Islem: {len(positions)}/{max_slots}",
        "",
        "<b>Son 24 Saat:</b>",
        f"  Kapanan: {len(closed)}  |  Karli: {win}  |  Zararli: {lose}",
        f"  Net K/Z: {_fmt_money(net)} USDT",
    ]

    if closed:
        lines.append("")
        lines.append("<b>Kapanan Islemler:</b>")
        for r in closed:
            lines.append(_fmt_closed_line(r))

    if positions:
        lines.append("")
        lines.append("<b>Acik Pozisyonlar:</b>")
        for p in positions:
            cp = prices.get(p.symbol, p.entry_price)
            lines.append(_fmt_position_line(p, cp))

    return "\n".join(lines)
