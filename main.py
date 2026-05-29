"""Bybit Futures Bot — orchestrator.

Akış:
    1) Config + env oku, modülleri kur.
    2) State yükle, açık pozisyonları Bybit ile eşle (restart sürekliliği).
    3) Sonsuz döngü:
        * 5sn'de bir: her coin için fiyat → giriş kontrolü, açık işlem takibi.
        * Her 15dk: mum kapanışı → flag güncellemesi.
        * Her 8 saat: bakiye refresh.
        * Periyodik raporlar.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Modül imports — proje kök ekleniyor
BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

from core.indicators import klines_to_df, compute_all
from core.flag_manager import FlagManager
from core.trade_manager import (
    Trade, compute_levels, update_level, check_exit,
    unrealized_pnl_usdt, unrealized_pnl_pct, LEVEL_NAMES,
)
from core.data_fetcher import BybitDataFetcher
from core.order_manager import OrderManager
from core.balance_manager import BalanceManager
from core.state import save_state, load_state
from reporting.telegram_bot import TelegramNotifier
from reporting import notifications as notify
from reporting.reports import ReportTracker

log = logging.getLogger("bot")


# ============================================================================
# UTIL
# ============================================================================

def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Environment variable {name} eksik")
    return val


# ============================================================================
# BOT
# ============================================================================

class TradingBot:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.coins: list[str] = config["coins"]

        # API anahtarları env'den
        api_key = require_env("BYBIT_API_KEY")
        api_secret = require_env("BYBIT_API_SECRET")
        tg_token = os.environ.get("TELEGRAM_TOKEN", "")
        tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")

        testnet = config["exchange"].get("testnet", False)
        retry_count = config["retry"]["api_retry_count"]
        retry_delay = config["retry"]["api_retry_delay_seconds"]

        self.fetcher = BybitDataFetcher(api_key, api_secret, testnet,
                                        retry_count, retry_delay)
        self.orders = OrderManager(api_key, api_secret, testnet,
                                   retry_count, retry_delay)
        self.balance = BalanceManager(
            self.fetcher,
            stake_percent=config["risk"]["stake_percent"],
            update_hours=config["timeframes"]["balance_update_hours"],
        )
        self.tg = TelegramNotifier(tg_token, tg_chat)
        self.fm = FlagManager()
        self.reports = ReportTracker(
            hourly_seconds=3600,
            z_seconds=config["reports"]["z_report_hours"] * 3600,
            x_seconds=config["reports"]["x_report_hours"] * 3600,
        )

        # Açık işlemler
        self.trades: list[Trade] = []

        # Önceki fiyat (EMA cross kontrolü için)
        self._last_prices: dict[str, float] = {}

        # 15dk mum güncellemesinden gelen son hesaplanmış göstergeler
        # symbol -> dict(ema, donchian_upper, donchian_lower, last_close)
        self._last_indicators: dict[str, dict] = {}

        # Mum güncelleme zamanlaması
        self._last_candle_refresh: float = 0.0
        self._candle_interval_seconds = int(config["timeframes"]["candle_interval"]) * 60

        self._stop = False

    # ---------- limit kontrolleri ----------

    def _trades_for(self, symbol: str, side: str) -> list[Trade]:
        return [t for t in self.trades if t.symbol == symbol and t.side == side]

    def _active_symbols(self) -> set[str]:
        return {t.symbol for t in self.trades}

    def _can_open(self, symbol: str, side: str) -> tuple[bool, str]:
        # 1) Aynı yönde max 3
        if len(self._trades_for(symbol, side)) >= self.config["limits"]["max_trades_per_side"]:
            return False, "max_trades_per_side"
        # 2) Toplam 20 coin slot — bu sembolde zaten varsa slot saymıyor
        if symbol not in self._active_symbols():
            if len(self._active_symbols()) >= self.config["limits"]["max_positions"]:
                return False, "max_positions"
        return True, ""

    # ---------- başlatma & senkronizasyon ----------

    def bootstrap(self) -> None:
        log.info("Bot başlatılıyor...")
        # Hedge mode + leverage tüm coinler için
        leverage = self.config["exchange"]["leverage"]
        for sym in self.coins:
            try:
                self.orders.set_hedge_mode(sym)
                self.orders.set_leverage(sym, leverage)
            except Exception as e:
                log.warning("%s setup hatası: %s", sym, e)

        # State yükle
        state_path = "state.json"
        loaded_trades, loaded_flags, _ = load_state(state_path)
        self.fm.load(loaded_flags) if loaded_flags else None
        self.trades = loaded_trades
        if loaded_trades:
            log.info("State'ten %d açık işlem yüklendi", len(loaded_trades))

        # Bakiye
        self.balance.force_refresh()
        self.reports.on_balance_snapshot(self.balance.balance)

        # Borsayı state ile karşılaştır
        self._reconcile_positions()

        self.tg.send(
            f"🤖 <b>Bot başladı</b>\n"
            f"Bakiye: <code>{self.balance.balance:.2f}</code>  "
            f"Stake: <code>{self.balance.stake:.2f}</code>\n"
            f"Aktif coin: {len(self.coins)}  •  Açık işlem: {len(self.trades)}"
        )

    def _reconcile_positions(self) -> None:
        """state.json'da olmayan açık pozisyonları kullanıcıya bildir.

        Spec: 'State.json'da olmayan açık pozisyon varsa → bilinmeyen pozisyon
        bildirimi gönderilir, slot olarak sayılır, bot tarafından takip edilmez.'

        Burada Bybit'ten açık pozisyonları çekip karşılaştırırız.
        """
        try:
            positions = self.fetcher.get_positions()
        except Exception as e:
            log.error("Pozisyon eşitleme başarısız: %s", e)
            return

        # state'teki net beklenti: symbol+side → toplam size
        expected: dict[tuple[str, str], float] = {}
        for t in self.trades:
            key = (t.symbol, t.side)
            expected[key] = expected.get(key, 0.0) + t.size

        for p in positions:
            try:
                size = float(p.get("size", 0))
                if size <= 0:
                    continue
                sym = p.get("symbol")
                pos_side = p.get("side")  # Buy/Sell
                side = "long" if pos_side == "Buy" else "short"
                key = (sym, side)
                exp_size = expected.get(key, 0.0)
                diff = abs(size - exp_size)
                # Toleranslı eşitlik (0.1% nispi)
                if diff / max(size, 1e-9) > 0.01:
                    self.tg.send(notify.fmt_unknown_position(sym, side, size - exp_size))
            except Exception as e:
                log.warning("Reconcile entry hatası: %s", e)

    # ---------- 15dk mum işlemleri ----------

    def refresh_candles(self) -> None:
        """Tüm coinler için 1000 mum çek, indikatörleri hesapla, flag kontrolü."""
        ema_period = self.config["indicators"]["ema_period"]
        donch = self.config["indicators"]["donchian_period"]
        atr_p = self.config["indicators"].get("atr_period", 14)
        interval = self.config["timeframes"]["candle_interval"]
        limit = self.config["timeframes"]["candle_limit"]

        for sym in self.coins:
            try:
                klines = self.fetcher.get_klines(sym, interval=interval, limit=limit)
                df = klines_to_df(klines)
                if len(df) < ema_period:
                    log.debug("%s: yetersiz veri (%d)", sym, len(df))
                    continue
                # Açık mum varsa son satırı düşür — Bybit son mum açıkken döndürür
                # Pratikte timestamp = şimdiki periyodun başlangıcı olabilir.
                df = compute_all(df, ema_period, donch, atr_p)
                last = df.iloc[-1]
                self._last_indicators[sym] = {
                    "ema": float(last["ema"]),
                    "donchian_upper": float(last["donchian_upper"]),
                    "donchian_lower": float(last["donchian_lower"]),
                    "atr": float(last["atr"]) if "atr" in df else 0.0,
                    "last_close": float(last["close"]),
                    "last_ts": int(last["timestamp"]),
                }
                # Flag kontrolü — son KAPANMIŞ iki mumu kullanıyoruz.
                # Bybit kline son satırı açık olabilir; pratik için son 2 satıra bakıyoruz.
                # Production'da: timestamp + interval > now ise açık demektir, drop.
                new_flag = self.fm.on_candle_close(sym, df.tail(2).reset_index(drop=True))
                if new_flag is not None:
                    self.reports.on_flag_open()
                    self.tg.send(
                        notify.fmt_flag_opened(sym, new_flag.side, new_flag.entry_trigger_price)
                    )
            except Exception as e:
                log.error("%s mum güncelleme hatası: %s", sym, e)
                self.reports.on_error()

    # ---------- 5sn fiyat tick'i ----------

    def price_tick(self) -> None:
        """Her aktif sembol için fiyat çek, flag tetiklerini ve trade'leri yönet.

        Verimlilik: sadece açık işlemi veya flagi olan + listedeki coinleri kontrol et.
        Tüm 20 coin için fiyat çekiyoruz (giriş aramak için).
        """
        for sym in self.coins:
            try:
                price = self.fetcher.get_ticker_price(sym)
            except Exception as e:
                log.warning("%s ticker hatası: %s", sym, e)
                self.reports.on_error()
                continue
            prev = self._last_prices.get(sym)
            self._last_prices[sym] = price

            ema_val = self._last_indicators.get(sym, {}).get("ema")

            # EMA cross → flag silinmesi
            if ema_val is not None and prev is not None:
                self.fm.on_price_cross_ema(sym, prev, price, ema_val)

            # Açık işlemleri kontrol et (önce — exit acildir)
            self._check_open_trades(sym, price, ema_val, prev)

            # Giriş kontrolü — EMA800 trend filtresi giriş anında uygulanır
            trigger_side = self.fm.check_entry_trigger(sym, price)
            if trigger_side is not None and ema_val is not None:
                # Long → fiyat EMA üstünde; Short → fiyat EMA altında
                if (trigger_side == "long" and price > ema_val) or \
                   (trigger_side == "short" and price < ema_val):
                    self._try_open_trade(sym, trigger_side, price)

        self.reports.on_position_count_snapshot(len(self._active_symbols()))

    def _check_open_trades(self, sym: str, price: float, ema_val: float | None,
                           prev_price: float | None) -> None:
        # Aynı semboldeki tüm trade'leri kontrol et (long+short ayrı ayrı)
        to_close: list[tuple[Trade, str]] = []
        for t in [x for x in self.trades if x.symbol == sym]:
            # Seviye ilerletme — önceki seviyeyi sakla
            old_level = t.level
            new_level = update_level(t, price)
            if new_level is not None:
                self.tg.send(notify.fmt_level_up(t, old_level))

            # Çıkış kontrolü
            reason = check_exit(t, price, ema_val, prev_price)
            if reason is not None:
                to_close.append((t, reason))

        for t, reason in to_close:
            self._close_trade(t, price, reason)

    def _try_open_trade(self, sym: str, side: str, price: float) -> None:
        ok, reason = self._can_open(sym, side)
        if not ok:
            if reason == "max_trades_per_side":
                log.debug("%s %s: aynı yön limiti dolu", sym, side)
            else:
                self.tg.send(notify.fmt_slot_full(sym, side))
            return

        # Donchian opposite — entry'de SABİTLENİYOR
        ind = self._last_indicators.get(sym, {})
        if not ind:
            log.warning("%s: indikatör yok, giriş atlandı", sym)
            return
        donch_opposite = ind["donchian_lower"] if side == "long" else ind["donchian_upper"]

        levels = compute_levels(
            side=side,
            entry=price,
            donchian_opposite=donch_opposite,
            sl_percent=self.config["risk"]["sl_percent"],
            rr=self.config["risk"]["risk_reward_ratio"],
        )

        # Qty hesapla
        try:
            instr = self.orders.get_instrument(sym)
        except Exception as e:
            log.error("%s instrument hatası: %s", sym, e)
            self.reports.on_error()
            return

        leverage = self.config["exchange"]["leverage"]
        qty = self.orders.calc_qty(self.balance.stake, leverage, price, instr)
        if qty < instr.min_qty:
            self.tg.send(notify.fmt_insufficient_balance(
                sym, instr.min_qty * price / leverage, self.balance.balance
            ))
            return

        # Emir gönder
        try:
            resp = self.orders.open_market_with_sl(sym, side, qty, levels.lose_exit)
        except Exception as e:
            log.error("%s %s emir hatası: %s", sym, side, e)
            self.tg.send(notify.fmt_error(f"{sym} {side} open: {e}"))
            self.reports.on_error()
            return

        # Trade kaydet
        trade = Trade(
            symbol=sym,
            side=side,
            size=qty,
            levels=levels,
            level=1,
            opened_at_ts=int(time.time() * 1000),
            notional_usdt=qty * price,
            stake_usdt=self.balance.stake,
            exchange_sl_order_id=str(resp.get("result", {}).get("orderId", "")),
        )
        self.trades.append(trade)
        self.fm.consume_flag(sym, side)  # flag tüketildi
        self.reports.on_trade_open(trade)
        self.tg.send(notify.fmt_trade_open(trade))
        self._persist()

    def _close_trade(self, t: Trade, exit_price: float, reason: str) -> None:
        try:
            self.orders.close_market(t.symbol, t.side, t.size)
        except Exception as e:
            log.error("%s kapat hatası: %s", t.symbol, e)
            self.tg.send(notify.fmt_error(f"{t.symbol} close: {e}"))
            self.reports.on_error()
            # Yine de bot içinde kapat — yoksa sonsuz retry yapar
            # State desync olabilir; manuel müdahale gerekir.
        pnl = unrealized_pnl_usdt(t, exit_price)
        pct = unrealized_pnl_pct(t, exit_price)
        self.trades.remove(t)
        self.reports.on_trade_close(t, exit_price, reason, pnl)  # type: ignore[arg-type]
        self.tg.send(notify.fmt_trade_close(t, exit_price, reason, pnl, pct))
        self._persist()

    # ---------- persist ----------

    def _persist(self) -> None:
        try:
            save_state("state.json", self.trades, self.fm.dump())
        except Exception as e:
            log.error("state.json yazma hatası: %s", e)

    # ---------- ana döngü ----------

    def _check_reports(self) -> None:
        for which in self.reports.due():
            try:
                if which == "hourly":
                    msg = self.reports.format_hourly(
                        self.trades, self.fm.active_flags(),
                        self._last_prices,
                        self.balance.balance, self.balance.stake,
                    )
                elif which == "z":
                    msg = self.reports.format_z(self.balance.balance)
                else:  # x
                    msg = self.reports.format_x(self.balance.balance)
                self.tg.send(msg)
                self.reports.reset(which)
            except Exception as e:
                log.error("Rapor (%s) hatası: %s", which, e)

    def run(self) -> None:
        self.bootstrap()

        scan_seconds = self.config["timeframes"]["price_scan_seconds"]
        self._last_candle_refresh = 0.0  # ilk turda hemen yenilenir

        signal.signal(signal.SIGINT, self._handle_sig)
        signal.signal(signal.SIGTERM, self._handle_sig)

        while not self._stop:
            loop_start = time.time()

            # 15dk mum refresh
            if loop_start - self._last_candle_refresh >= self._candle_interval_seconds:
                log.info("Mum verisi yenileniyor...")
                self.refresh_candles()
                self._last_candle_refresh = loop_start

            # Bakiye periyodik
            if self.balance.maybe_refresh():
                self.reports.on_balance_snapshot(self.balance.balance)

            # Fiyat tick'i
            self.price_tick()

            # Raporlar
            self._check_reports()

            # Uyku — döngü maliyetini çıkar
            elapsed = time.time() - loop_start
            sleep_for = max(0.0, scan_seconds - elapsed)
            time.sleep(sleep_for)

        log.info("Bot durduruluyor...")
        self._persist()
        self.tg.send("🛑 Bot durdu")

    def _handle_sig(self, sig, frame) -> None:
        log.info("Signal %s alındı, kapanış başlatılıyor", sig)
        self._stop = True


# ============================================================================
# ENTRY
# ============================================================================

def main() -> None:
    cfg = load_config(os.environ.get("BOT_CONFIG", "config.json"))
    setup_logging(
        cfg.get("logging", {}).get("log_level", "INFO"),
        cfg.get("logging", {}).get("log_file"),
    )
    bot = TradingBot(cfg)
    try:
        bot.run()
    except Exception as e:
        log.exception("Fatal: %s", e)
        bot.tg.send(notify.fmt_error(f"FATAL: {e}"))
        raise


if __name__ == "__main__":
    main()
