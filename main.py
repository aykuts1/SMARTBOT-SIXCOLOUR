"""
ATR TUNNEL Bot - Ana Giriş
Bot başlatma, ana tarama döngüsü, sinyal işleme ve raporlama.
"""
import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd

import config
import strategy
import telegram_bot as tg_module
from bybit_client import BybitClient
from position_manager import (
    Position, StateManager, TradeRecord
)

# ============================================================
# LOG AYARLARI
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("atr_tunnel")


# ============================================================
# KLINE CACHE
# ============================================================

class KlineCache:
    """Mum verilerini cache'ler. Sadece yeni mum geldiğinde refetch.

    Bant değerleri kapanmış mumlardan hesaplandığı için, mum kapanmadan
    cache'i yenilemenin anlamı yok.
    """

    def __init__(self, bybit: BybitClient, interval: str):
        self.bybit = bybit
        self.interval = interval
        self.data: dict[str, pd.DataFrame] = {}
        self.last_fetch: dict[str, float] = {}

    def get(self, symbol: str) -> Optional[pd.DataFrame]:
        """Sembol için kline döndür. Gerekirse refetch."""
        now = time.time()
        last = self.last_fetch.get(symbol, 0)

        # İlk kez veya timeframe süresi geçti
        tf_sec = config.get_timeframe_seconds()
        if symbol not in self.data or (now - last) >= tf_sec:
            try:
                df = self.bybit.get_kline(
                    symbol, self.interval, limit=200
                )
                if len(df) < 50:
                    logger.warning(f"{symbol} çok az mum: {len(df)}")
                    return None
                self.data[symbol] = df
                self.last_fetch[symbol] = now
            except Exception as e:
                logger.error(f"{symbol} kline fetch hatası: {e}")
                return self.data.get(symbol)  # Eski cache'i kullan
        return self.data[symbol]

    def get_closed_only(self, symbol: str) -> Optional[pd.DataFrame]:
        """Sadece kapanmış mumlar (son satır = forming, atılır)."""
        df = self.get(symbol)
        if df is None or len(df) < 2:
            return None
        return df.iloc[:-1].copy()


# ============================================================
# BOT
# ============================================================

class AtrTunnelBot:

    def __init__(self):
        self.bybit = BybitClient()
        self.tg = tg_module.TelegramBot()
        self.state = StateManager()
        self.kline_cache: Optional[KlineCache] = None

        # API sağlık durumu
        self.api_healthy = True

        # Rapor zamanlamaları
        self.last_hourly_report: Optional[datetime] = None
        self.last_12h_report: Optional[datetime] = None
        self.last_daily_report: Optional[datetime] = None

    # ============================================================
    # BAŞLATMA
    # ============================================================

    def start(self) -> None:
        logger.info("=" * 50)
        logger.info("ATR TUNNEL BOT BAŞLIYOR")
        logger.info("=" * 50)

        # API kontrolü
        if not self.bybit.ping():
            logger.error("Bybit API'ye bağlanılamadı, çıkılıyor.")
            return

        # Bakiye okuma ve stake kilitleme
        balance = self.bybit.get_balance()
        if balance <= 0:
            logger.error(f"Bakiye geçersiz: {balance}, çıkılıyor.")
            return

        self.state.start_balance = balance
        self.state.locked_stake = balance * config.STAKE_PERCENTAGE
        logger.info(f"Bakiye: {balance:.2f} USDT")
        logger.info(f"Stake: {self.state.locked_stake:.2f} USDT (sabit)")

        # Tüm semboller için isolated + leverage
        logger.info("Semboller hazırlanıyor...")
        for sym in config.SYMBOLS:
            self.bybit.set_isolated_margin(sym, config.LEVERAGE)
            self.bybit.set_leverage(sym, config.LEVERAGE)
            # Instrument info'yu önbelleğe çek
            self.bybit.get_instrument_info(sym)

        # Kline cache başlat
        self.kline_cache = KlineCache(
            self.bybit, config.get_bybit_interval()
        )

        # Telegram başlangıç bildirimi
        mode = "TESTNET" if config.BYBIT_TESTNET else "MAINNET"
        tg_module.notify_bot_started(
            self.tg, balance, self.state.locked_stake, mode
        )

        logger.info("Hazır. Ana döngü başlıyor.")
        self._main_loop()

    # ============================================================
    # ANA DÖNGÜ
    # ============================================================

    def _main_loop(self) -> None:
        while True:
            scan_start = time.time()
            try:
                self._scan_cycle()
            except Exception as e:
                logger.exception(f"Scan döngüsü hatası: {e}")

            # Rapor zamanlaması
            self._check_reports()

            # Health check
            self._check_api_health()

            elapsed = time.time() - scan_start
            sleep_time = max(0.0, config.SCAN_INTERVAL_SEC - elapsed)
            time.sleep(sleep_time)

    def _scan_cycle(self) -> None:
        # Tüm tickers'ı tek API çağrısıyla al
        tickers = self.bybit.get_all_tickers(config.SYMBOLS)
        if not tickers:
            logger.warning("Ticker alınamadı, scan atlanıyor.")
            return

        for symbol in config.SYMBOLS:
            try:
                current_price = tickers.get(symbol)
                if not current_price:
                    continue

                # Önce açık pozisyonu kontrol et (çıkış)
                if self.state.has_position(symbol):
                    self._check_position(symbol, current_price)
                    continue

                # Yeni sinyal kontrolü
                self._check_signal(symbol, current_price)

            except Exception as e:
                logger.exception(f"{symbol} işlem hatası: {e}")

    # ============================================================
    # GİRİŞ SİNYALİ KONTROLÜ
    # ============================================================

    def _check_signal(self, symbol: str, current_price: float) -> None:
        df_closed = self.kline_cache.get_closed_only(symbol)
        if df_closed is None or len(df_closed) < max(config.EMA_PERIOD, config.ATR_PERIOD) + 5:
            return

        flag_side = self.state.get_flag_side(symbol)
        action, bands = strategy.check_entry_signal(
            df_closed, current_price, flag_side
        )

        if action is None:
            return

        # Flag iptal
        if action == "long_cancel" or action == "short_cancel":
            self.state.clear_flag(symbol)
            logger.info(f"{symbol} flag iptal ({action})")
            return

        # Flag set
        if action == "long_flag":
            self.state.set_flag(symbol, "long")
            logger.info(f"{symbol} LONG flag açıldı")
            return
        if action == "short_flag":
            self.state.set_flag(symbol, "short")
            logger.info(f"{symbol} SHORT flag açıldı")
            return

        # Giriş eylemi
        if action in ("long_direct", "long_enter"):
            self._try_open_position(symbol, "long", bands)
        elif action in ("short_direct", "short_enter"):
            self._try_open_position(symbol, "short", bands)

    def _try_open_position(self, symbol: str, side: str, bands: dict) -> None:
        """Pozisyon açma denemesi.

        Slot kontrolü ve coin kontrolü yapılır, ardından entry order loop'a girilir.
        """
        # Slot kontrolü
        if not self.state.can_open_new_position():
            self.state.add_failed_signal(symbol, side, "slot_full")
            tg_module.notify_signal_failed(self.tg, symbol, side, "slot_full")
            self.state.clear_flag(symbol)
            return

        # Aynı coinde pozisyon
        if self.state.has_position(symbol):
            self.state.add_failed_signal(symbol, side, "already_open")
            tg_module.notify_signal_failed(self.tg, symbol, side, "already_open")
            self.state.clear_flag(symbol)
            return

        # Entry order loop
        self._execute_entry(symbol, side, bands)
        # Flag temizle (başarılı veya değil)
        self.state.clear_flag(symbol)

    def _execute_entry(self, symbol: str, side: str, bands: dict) -> bool:
        """Market emir ile anlık giriş."""
        order_side = "Buy" if side == "long" else "Sell"

        # Güncel fiyat al
        cur_price = self.bybit.get_last_price(symbol)
        if cur_price is None:
            logger.warning(f"{symbol} fiyat alınamadı, giriş atlandı")
            self.state.add_failed_signal(symbol, side, "api_error")
            tg_module.notify_signal_failed(self.tg, symbol, side, "api_error")
            return False

        # Quantity hesabı
        position_size = self.state.locked_stake * config.LEVERAGE
        raw_qty = position_size / cur_price
        qty = self.bybit.round_qty(symbol, raw_qty)
        min_qty = self.bybit.get_min_qty(symbol)
        if qty < min_qty:
            logger.warning(f"{symbol} qty {qty} < min {min_qty}")
            self.state.add_failed_signal(symbol, side, "insufficient_qty")
            tg_module.notify_signal_failed(self.tg, symbol, side, "insufficient_qty")
            return False

        # Market emir gönder
        order_id = self.bybit.place_market_order(symbol, order_side, qty, reduce_only=False)
        if not order_id:
            logger.warning(f"{symbol} market emir gönderilemedi")
            self.state.add_failed_signal(symbol, side, "api_error")
            tg_module.notify_signal_failed(self.tg, symbol, side, "api_error")
            return False

        # 2 saniye bekle, dolduğunu varsay
        time.sleep(2)

        # Borsadan gerçek giriş fiyatını al
        live_pos = self.bybit.get_position(symbol)
        if live_pos:
            avg_price = float(live_pos.get("avgPrice") or cur_price)
            filled_qty = float(live_pos.get("size") or qty)
        else:
            avg_price = cur_price
            filled_qty = qty

        self._register_position(symbol, side, avg_price, filled_qty, bands)
        return True

    def _register_position(self, symbol: str, side: str,
                          entry_price: float, qty: float,
                          bands: dict) -> None:
        """Yeni pozisyonu kaydet, SL set et, Telegram bildirimi gönder."""
        position_size = qty * entry_price

        # SL fiyatı
        if side == "long":
            sl_price = entry_price * (1 - config.STOP_LOSS_PERCENT)
        else:
            sl_price = entry_price * (1 + config.STOP_LOSS_PERCENT)
        sl_price = self.bybit.round_price(symbol, sl_price)

        pos = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_atr=bands["atr"],
            quantity=qty,
            position_size=position_size,
            stake=self.state.locked_stake,
            sl_price=sl_price,
            best_price=entry_price,
        )
        self.state.add_position(pos)

        # SL set (borsa tarafında)
        self.bybit.set_stop_loss(symbol, sl_price)

        # Telegram bildirimi
        tg_module.notify_trade_opened(self.tg, pos)
        logger.info(f"{symbol} {side.upper()} açıldı @ {entry_price}")

    # ============================================================
    # POZİSYON / ÇIKIŞ KONTROLÜ
    # ============================================================

    def _check_position(self, symbol: str, current_price: float) -> None:
        pos = self.state.get_position(symbol)
        if not pos:
            return

        # Borsada SL ile kapanmış mı?
        live_pos = self.bybit.get_position(symbol)
        if live_pos is None:
            # Pozisyon borsada yok artık (SL tetiklendi olabilir)
            self._handle_external_close(pos)
            return

        # Bant değerleri
        df_closed = self.kline_cache.get_closed_only(symbol)
        if df_closed is None or len(df_closed) < max(config.EMA_PERIOD, config.ATR_PERIOD) + 5:
            return
        bands = strategy.calc_bands(df_closed)

        # Önce çıkış kontrolü (seviyenin gerektirdiği)
        exit_reason = strategy.check_exit_conditions(pos, current_price, bands)
        if exit_reason:
            self._execute_exit(pos, exit_reason, bands)
            return

        # Seviye güncelle (kâr ilerledikçe)
        level_change = strategy.update_position_levels(pos, current_price, bands)
        if level_change:
            metrics = strategy.calc_profit_metrics(pos, current_price)
            if level_change == "level_up_be":
                tg_module.notify_breakeven_active(self.tg, pos, current_price, metrics)
            elif level_change == "level_up_ce":
                tg_module.notify_ce_trail_active(self.tg, pos, current_price, metrics)
            elif level_change == "level_up_winrate":
                tg_module.notify_winrate_active(self.tg, pos, current_price, metrics)

    def _handle_external_close(self, pos: Position) -> None:
        """Pozisyon borsada artık yok — SL tetiklenmiş olabilir.

        Bybit'ten son işlemi alıp PnL ve sebep belirlenebilir, ancak basit
        yaklaşım: SL fiyatından kapandığını varsay.
        """
        exit_price = pos.sl_price  # tahmini
        trade = self._build_trade_record(pos, exit_price, "stoploss")
        self.state.add_trade(trade)
        self.state.remove_position(pos.symbol)
        tg_module.notify_stoploss(self.tg, trade)
        logger.info(f"{pos.symbol} SL tetiklenmiş, pozisyon kapatıldı")

    def _execute_exit(self, pos: Position, exit_reason: str, bands: dict) -> None:
        """Market emir ile anlık çıkış."""
        close_side = "Sell" if pos.side == "long" else "Buy"

        order_id = self.bybit.place_market_order(
            pos.symbol, close_side, pos.quantity, reduce_only=True
        )
        if not order_id:
            logger.warning(f"{pos.symbol} çıkış market emri gönderilemedi, tekrar deneniyor...")
            time.sleep(2)
            order_id = self.bybit.place_market_order(
                pos.symbol, close_side, pos.quantity, reduce_only=True
            )
            if not order_id:
                logger.error(f"{pos.symbol} çıkış başarısız!")
                return

        # 2 saniye bekle
        time.sleep(2)
        cur = self.bybit.get_last_price(pos.symbol) or pos.entry_price
        self._finalize_exit(pos, cur, exit_reason)

    def _finalize_exit(self, pos: Position, exit_price: float,
                      exit_reason: str) -> None:
        trade = self._build_trade_record(pos, exit_price, exit_reason)
        self.state.add_trade(trade)
        self.state.remove_position(pos.symbol)
        tg_module.notify_trade_closed(self.tg, trade)
        logger.info(
            f"{pos.symbol} kapatıldı @ {exit_price} | "
            f"{exit_reason} | PNL: {trade.pnl:.2f}"
        )

    def _build_trade_record(self, pos: Position, exit_price: float,
                           exit_type: str) -> TradeRecord:
        if pos.side == "long":
            diff = exit_price - pos.entry_price
        else:
            diff = pos.entry_price - exit_price

        pnl = (diff / pos.entry_price) * pos.position_size if pos.entry_price else 0
        pnl_pct = (diff / pos.entry_price * 100) if pos.entry_price else 0
        pnl_atr = (diff / pos.entry_atr) if pos.entry_atr else 0

        return TradeRecord(
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            position_size=pos.position_size,
            stake=pos.stake,
            entry_atr=pos.entry_atr,
            pnl=pnl,
            pnl_pct=pnl_pct,
            pnl_atr=pnl_atr,
            open_time=pos.open_time,
            close_time=datetime.now(),
            exit_type=exit_type,
            max_level=pos.level,
        )

    # ============================================================
    # RAPORLAR
    # ============================================================

    def _check_reports(self) -> None:
        now = datetime.now()

        # Saatlik: dakika 0'da
        if now.minute == 0 and (
            self.last_hourly_report is None
            or self.last_hourly_report.hour != now.hour
            or self.last_hourly_report.date() != now.date()
        ):
            try:
                tickers = self.bybit.get_all_tickers(config.SYMBOLS)
                text = tg_module.build_hourly_report(self.state, tickers)
                self.tg.send(text)
            except Exception as e:
                logger.exception(f"Saatlik rapor hatası: {e}")
            self.last_hourly_report = now

        # 12 saatlik: 00:00 ve 12:00
        if now.minute == 0 and now.hour in (0, 12) and (
            self.last_12h_report is None
            or self.last_12h_report.hour != now.hour
            or self.last_12h_report.date() != now.date()
        ):
            try:
                text = tg_module.build_12h_report(self.state)
                self.tg.send(text)
            except Exception as e:
                logger.exception(f"12h rapor hatası: {e}")
            self.last_12h_report = now

        # Günlük Z: 09:00
        if now.hour == 9 and now.minute == 0 and (
            self.last_daily_report is None
            or self.last_daily_report.date() != now.date()
        ):
            try:
                text = tg_module.build_daily_z_report(self.state)
                self.tg.send(text)
            except Exception as e:
                logger.exception(f"Günlük Z rapor hatası: {e}")
            self.last_daily_report = now

    # ============================================================
    # SAĞLIK KONTROLÜ
    # ============================================================

    def _check_api_health(self) -> None:
        ok = self.bybit.ping()
        if ok and not self.api_healthy:
            self.api_healthy = True
            tg_module.notify_api_restored(self.tg)
        elif not ok and self.api_healthy:
            self.api_healthy = False
            tg_module.notify_api_lost(self.tg)


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    bot = AtrTunnelBot()
    try:
        bot.start()
    except KeyboardInterrupt:
        logger.info("Klavye ile durduruldu.")
    except Exception as e:
        logger.exception(f"Beklenmeyen hata: {e}")


if __name__ == "__main__":
    main()
