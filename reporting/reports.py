"""Periyodik raporlar.

Bot içinde 'ReportTracker' tutar — açılan/kapanan işlemleri biriktirir,
süre dolunca rapor üretir, biriktirdiklerini sıfırlar.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from core.trade_manager import Trade, LEVEL_NAMES, ExitReason


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    side: str
    opened_at: float
    closed_at: float | None
    entry: float
    exit_price: float | None
    size: float
    stake_usdt: float
    notional_usdt: float
    pnl_usdt: float | None
    exit_reason: ExitReason | None
    max_level_reached: int = 1


@dataclass
class PeriodStats:
    period_name: str  # 'hourly' / 'z' / 'x'
    period_start: float
    opened_trades: list[TradeRecord] = field(default_factory=list)
    closed_trades: list[TradeRecord] = field(default_factory=list)
    errors: int = 0
    balance_start: float = 0.0
    balance_peak: float = 0.0
    balance_low: float = 0.0
    max_concurrent_positions: int = 0
    flags_created: int = 0
    flags_converted: int = 0  # flag açılıp işleme dönüşen sayısı


class ReportTracker:
    """Tek bir merkezi kayıtçı; istenen periyod (hourly/z/x) için snapshot üretir."""

    def __init__(self, hourly_seconds: int = 3600,
                 z_seconds: int = 8 * 3600,
                 x_seconds: int = 24 * 3600) -> None:
        self.hourly_seconds = hourly_seconds
        self.z_seconds = z_seconds
        self.x_seconds = x_seconds
        now = time.time()
        self.hourly = PeriodStats("hourly", now)
        self.z = PeriodStats("z", now)
        self.x = PeriodStats("x", now)

    # ---- events ----

    def on_trade_open(self, trade: Trade) -> None:
        rec = TradeRecord(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            side=trade.side,
            opened_at=time.time(),
            closed_at=None,
            entry=trade.levels.entry,
            exit_price=None,
            size=trade.size,
            stake_usdt=trade.stake_usdt,
            notional_usdt=trade.notional_usdt,
            pnl_usdt=None,
            exit_reason=None,
        )
        for p in (self.hourly, self.z, self.x):
            p.opened_trades.append(rec)
            p.flags_converted += 1

    def on_trade_close(self, trade: Trade, exit_price: float,
                       reason: ExitReason, pnl_usdt: float) -> None:
        rec = TradeRecord(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            side=trade.side,
            opened_at=trade.opened_at_ts / 1000 if trade.opened_at_ts > 1e9 else trade.opened_at_ts,
            closed_at=time.time(),
            entry=trade.levels.entry,
            exit_price=exit_price,
            size=trade.size,
            stake_usdt=trade.stake_usdt,
            notional_usdt=trade.notional_usdt,
            pnl_usdt=pnl_usdt,
            exit_reason=reason,
            max_level_reached=trade.level,
        )
        for p in (self.hourly, self.z, self.x):
            p.closed_trades.append(rec)

    def on_flag_open(self) -> None:
        for p in (self.hourly, self.z, self.x):
            p.flags_created += 1

    def on_error(self) -> None:
        for p in (self.hourly, self.z, self.x):
            p.errors += 1

    def on_balance_snapshot(self, balance: float) -> None:
        for p in (self.hourly, self.z, self.x):
            if p.balance_start == 0.0:
                p.balance_start = balance
                p.balance_peak = balance
                p.balance_low = balance
            else:
                p.balance_peak = max(p.balance_peak, balance)
                p.balance_low = min(p.balance_low, balance)

    def on_position_count_snapshot(self, count: int) -> None:
        for p in (self.hourly, self.z, self.x):
            p.max_concurrent_positions = max(p.max_concurrent_positions, count)

    # ---- periodic check ----

    def due(self) -> list[str]:
        """Hangi raporlar zamanı geldi (hourly/z/x). Tetikleyici bunları okuyup
        formatlayıp gönderir, sonra reset_X çağırır."""
        now = time.time()
        out = []
        if now - self.hourly.period_start >= self.hourly_seconds:
            out.append("hourly")
        if now - self.z.period_start >= self.z_seconds:
            out.append("z")
        if now - self.x.period_start >= self.x_seconds:
            out.append("x")
        return out

    def reset(self, which: str) -> None:
        now = time.time()
        if which == "hourly":
            self.hourly = PeriodStats("hourly", now)
        elif which == "z":
            self.z = PeriodStats("z", now)
        elif which == "x":
            self.x = PeriodStats("x", now)

    # ---- formatting ----

    def format_hourly(self, active_trades: list[Trade], active_flags: list,
                       current_prices: dict[str, float],
                       balance: float, stake: float) -> str:
        p = self.hourly
        lines = ["<b>📊 Saatlik Rapor</b>"]
        lines.append(f"Açık işlem: <b>{len(active_trades)}</b>")
        # Aktif coin sayısı (semboller)
        symbols = {t.symbol for t in active_trades}
        lines.append(f"Aktif coin: <b>{len(symbols)}</b>")
        lines.append(f"Bakiye: <code>{balance:.2f}</code>  •  Stake: <code>{stake:.2f}</code>")
        lines.append(f"Hata: <b>{p.errors}</b>")

        if active_flags:
            lines.append("\n<b>Aktif Flags:</b>")
            for f in active_flags[:10]:
                lines.append(f"  • {f.symbol} {f.side} @ {f.entry_trigger_price:.6f}")

        if active_trades:
            lines.append("\n<b>Açık Pozisyonlar:</b>")
            total_pnl = 0.0
            for t in active_trades[:20]:
                price = current_prices.get(t.symbol, t.levels.entry)
                if t.side == "long":
                    pnl = (price - t.levels.entry) * t.size
                else:
                    pnl = (t.levels.entry - price) * t.size
                total_pnl += pnl
                pct = (pnl / t.stake_usdt * 100.0) if t.stake_usdt > 0 else 0.0
                lines.append(
                    f"  • {t.symbol} {t.side[:1].upper()} "
                    f"L{t.level}({LEVEL_NAMES[t.level-1]}) "
                    f"PnL: {pnl:+.2f}USDT ({pct:+.1f}%)"
                )
            lines.append(f"\n<b>Toplam Açık PnL:</b> {total_pnl:+.2f} USDT")

        return "\n".join(lines)

    def format_z(self, balance_end: float) -> str:
        return self._format_period(self.z, "Z Raporu (8 Saatlik)", balance_end)

    def format_x(self, balance_end: float) -> str:
        return self._format_period(self.x, "X Raporu (24 Saatlik)", balance_end,
                                   detailed=True)

    def _format_period(self, p: PeriodStats, title: str, balance_end: float,
                       detailed: bool = False) -> str:
        closed = p.closed_trades
        wins = [t for t in closed if t.pnl_usdt is not None and t.pnl_usdt > 0]
        losses = [t for t in closed if t.pnl_usdt is not None and t.pnl_usdt <= 0]
        net = sum(t.pnl_usdt or 0.0 for t in closed)

        lines = [f"<b>📑 {title}</b>"]
        lines.append(f"Açılan: <b>{len(p.opened_trades)}</b>  •  Kapanan: <b>{len(closed)}</b>")
        lines.append(f"Kârlı: <b>{len(wins)}</b>  •  Zararlı: <b>{len(losses)}</b>")
        wr = (len(wins) / len(closed) * 100.0) if closed else 0.0
        lines.append(f"Win Rate: <b>{wr:.1f}%</b>")
        lines.append(f"Net PnL: <b>{net:+.2f} USDT</b>")
        lines.append(f"Bakiye: {p.balance_start:.2f} → {balance_end:.2f}")
        lines.append(f"Hata: <b>{p.errors}</b>")

        # Çıkış sebepleri
        if closed:
            reasons: dict[str, int] = defaultdict(int)
            for t in closed:
                if t.exit_reason:
                    reasons[t.exit_reason] += 1
            if reasons:
                rstr = "  ".join(f"{k}:{v}" for k, v in reasons.items())
                lines.append(f"Çıkışlar: {rstr}")

        # Süre istatistikleri
        durations = [
            (t.closed_at - t.opened_at) for t in closed
            if t.closed_at and t.opened_at
        ]
        if durations:
            avg = sum(durations) / len(durations) / 60.0
            longest = max(durations) / 60.0
            lines.append(f"Ort. süre: <b>{avg:.1f} dk</b>  •  En uzun: <b>{longest:.1f} dk</b>")

        # Coin bazlı PnL
        by_coin: dict[str, float] = defaultdict(float)
        for t in closed:
            by_coin[t.symbol] += (t.pnl_usdt or 0.0)
        if by_coin:
            top = sorted(by_coin.items(), key=lambda x: -x[1])[:5]
            bot = sorted(by_coin.items(), key=lambda x: x[1])[:3]
            lines.append("\n<b>En iyi:</b>")
            for s, v in top:
                lines.append(f"  • {s}: {v:+.2f}")
            if detailed:
                lines.append("<b>En kötü:</b>")
                for s, v in bot:
                    if v >= 0: break
                    lines.append(f"  • {s}: {v:+.2f}")

        if detailed:
            volume = sum(t.notional_usdt for t in p.opened_trades)
            lines.append(f"Hacim: {volume:.0f} USDT")
            lines.append(f"Max eş zamanlı pozisyon: {p.max_concurrent_positions}")
            lines.append(f"Flag oluşan: {p.flags_created}  →  İşleme dönüşen: {p.flags_converted}")
            lines.append(f"Bakiye aralığı: {p.balance_low:.2f} – {p.balance_peak:.2f}")

        return "\n".join(lines)
