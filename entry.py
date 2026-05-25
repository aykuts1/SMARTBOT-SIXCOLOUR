"""
SMARTBOT REDBLUE — entry.py
Kirmizi ve Mavi giris threadleri.
Her 5 saniyede tarama yapar: flag acma/silme + islem girisi.
"""

import time
import threading
from datetime import datetime

from state import state
from band import calculate_bands
from flag import (
    check_kirmizi_long_flag_open, check_kirmizi_long_flag_close,
    check_kirmizi_short_flag_open, check_kirmizi_short_flag_close,
    check_mavi_long_flag_open, check_mavi_long_flag_close,
    check_mavi_short_flag_open, check_mavi_short_flag_close,
    check_kirmizi_long_entry, check_kirmizi_short_entry,
    check_mavi_long_entry, check_mavi_short_entry,
)
from bybit_client import BybitClient, round_qty, round_price
from telegram_notifier import (
    notifier, msg_trade_opened, msg_error, msg_insufficient_balance,
    msg_slot_full, msg_direction_changed,
)


class EntryThread(threading.Thread):
    """
    color: "kirmizi" veya "mavi"
    """

    def __init__(self, color: str, config: dict, bybit: BybitClient, stop_event: threading.Event):
        super().__init__(daemon=True, name=f"{color.capitalize()}_Entry")
        self.color = color
        self.config = config
        self.bybit = bybit
        self.stop_event = stop_event
        self.scan_interval = config["scan"]["interval_seconds"]
        self.memory_seconds = config["flag"]["price_memory_seconds"]
        self.lookback = config["flag"]["crossover_lookback_count"]
        self.timeframe = config["band"]["timeframe"]
        self.max_slots = config["slot"]["max_open_positions"]
        self.leverage = config["order"]["leverage"]
        self.sl_pct = config["order"]["sl_percent"] / 100.0
        self.coins = config["coins"]["list"]

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.scan_all_coins()
            except Exception as e:
                notifier.send(msg_error(self.name, "-", "Genel Tarama Hatasi", str(e)))
            self.stop_event.wait(self.scan_interval)

    def scan_all_coins(self):
        for coin in self.coins:
            if self.stop_event.is_set():
                return
            try:
                self.scan_coin(coin)
            except Exception as e:
                notifier.send(msg_error(self.name, coin, "Coin Tarama Hatasi", str(e)))

    def scan_coin(self, coin: str):
        # Cache'den fiyat ve kline oku
        price = state.get_cached_price(coin)
        klines = state.get_cached_klines(coin)

        # Cache hazir degilse (fetcher henuz yazmadi) atla
        if price is None or klines is None:
            return

        # Cache eskimisse (15 sn'den fazla) atla — fetcher sorun yasiyor olabilir
        if state.get_cache_age(coin) > 15:
            return

        bands = calculate_bands(klines, self.config)

        # 3. Fiyat hafizasi ekle (lookback icin onceki fiyatlar lazim — eklemeden ONCE oku)
        # En az 3 fiyat al (giris crossover icin), flag lookback'i daha buyukse onu kullan
        recent_prices = state.get_recent_prices(coin, max(self.lookback, 3))
        state.add_price(coin, price, self.memory_seconds)

        # 4. Flag yonetimi
        if self.color == "kirmizi":
            self.handle_kirmizi(coin, price, recent_prices, bands)
        else:
            self.handle_mavi(coin, price, recent_prices, bands, klines)

    # ──────────────────────────────────────────
    # KIRMIZI
    # ──────────────────────────────────────────

    def handle_kirmizi(self, coin: str, price: float, recent: list, bands: dict):
        # LONG
        long_flag = state.get_flag(coin, "kirmizi_long")
        if not long_flag:
            if check_kirmizi_long_flag_open(price, recent, bands):
                state.set_flag(coin, "kirmizi_long", True)
                long_flag = True
        else:
            if check_kirmizi_long_flag_close(price, recent, bands):
                state.set_flag(coin, "kirmizi_long", False)
                long_flag = False

        if long_flag and check_kirmizi_long_entry(price, recent, bands):
            self.try_open_trade(coin, "kirmizi", "long", bands)

        # SHORT
        short_flag = state.get_flag(coin, "kirmizi_short")
        if not short_flag:
            if check_kirmizi_short_flag_open(price, recent, bands):
                state.set_flag(coin, "kirmizi_short", True)
                short_flag = True
        else:
            if check_kirmizi_short_flag_close(price, recent, bands):
                state.set_flag(coin, "kirmizi_short", False)
                short_flag = False

        if short_flag and check_kirmizi_short_entry(price, recent, bands):
            self.try_open_trade(coin, "kirmizi", "short", bands)

    # ──────────────────────────────────────────
    # MAVI
    # ──────────────────────────────────────────

    def handle_mavi(self, coin: str, price: float, recent: list, bands: dict, klines: list):
        # LONG
        long_flag = state.get_flag(coin, "mavi_long")
        if not long_flag:
            if check_mavi_long_flag_open(price, recent, bands):
                state.set_flag(coin, "mavi_long", True)
                long_flag = True
        else:
            if check_mavi_long_flag_close(price, recent, bands):
                state.set_flag(coin, "mavi_long", False)
                long_flag = False

        if long_flag and check_mavi_long_entry(price, recent, bands):
            self.try_open_trade(coin, "mavi", "long", bands)

        # SHORT
        short_flag = state.get_flag(coin, "mavi_short")
        if not short_flag:
            if check_mavi_short_flag_open(price, recent, bands):
                state.set_flag(coin, "mavi_short", True)
                short_flag = True
        else:
            if check_mavi_short_flag_close(price, recent, bands):
                state.set_flag(coin, "mavi_short", False)
                short_flag = False

        if short_flag and check_mavi_short_entry(price, recent, bands):
            self.try_open_trade(coin, "mavi", "short", bands)

    # ──────────────────────────────────────────
    # ISLEM ACMA
    # ──────────────────────────────────────────

    def try_open_trade(self, coin: str, color: str, side: str, bands: dict):
        flag_name = f"{color}_{side}"

        # Slot dolu mu?
        if state.get_open_count() >= self.max_slots:
            notifier.send(msg_slot_full(coin, color, side, self.max_slots))
            return

        # Coinde acik islem var mi?
        existing = state.get_position(coin)
        direction_changed = False  # Yon degisimi flagi
        old_close_data = None      # Eski islemin kapanis bilgisi
        old_position = None        # Bildirim icin eski pozisyon
        if existing:
            # Ayni yon ise: zaten acik, hicbir sey yapma
            if existing["color"] == color and existing["side"] == side:
                return
            # Farkli yon: eski islemi kapat, yeni islem aciliyor
            try:
                self.bybit.close_position(coin, existing["order_side"], existing["qty"])
            except Exception as e:
                notifier.send(msg_error(self.name, coin, "Yon Degisimi - Kapatma Hatasi", str(e)))
                return

            # Eski pozisyon K/Z hesabi
            exit_price = state.get_cached_price(coin)
            if exit_price is None:
                exit_price = existing["entry_price"]
            entry = existing["entry_price"]
            qty_old = existing["qty"]
            if existing["side"] == "long":
                gross = (exit_price - entry) * qty_old
            else:
                gross = (entry - exit_price) * qty_old
            commission_old = existing["volume"] * self.config["commission"]["rate"]
            net = gross - commission_old

            old_close_data = {
                "exit_time": datetime.now(),
                "exit_price": exit_price,
                "exit_type": "YON DEGISIMI",
                "gross_pnl": gross,
                "net_pnl": net,
                "commission": commission_old,
            }
            old_position = existing
            state.remove_position(coin, old_close_data)
            direction_changed = True

        # Bakiye yeterli mi? (Kullanilabilir bakiye - acik pozisyon marjini dusuldukten sonra)
        try:
            balance = self.bybit.get_available_balance()
        except Exception as e:
            notifier.send(msg_error(self.name, coin, "Bakiye Sorgu Hatasi", str(e)))
            return

        stake = state.stake
        if balance < stake:
            notifier.send(msg_insufficient_balance(coin, color, side, balance, stake, state.get_open_count(), self.max_slots))
            return

        # Islem hacmi ve miktari
        volume = stake * self.leverage
        price = state.get_cached_price(coin)
        if price is None:
            return  # Cache yok, atla
        try:
            info = self.bybit.get_instrument_info(coin)
        except Exception as e:
            notifier.send(msg_error(self.name, coin, "Sembol Bilgi Hatasi", str(e)))
            return

        raw_qty = volume / price
        qty = round_qty(raw_qty, info["qty_step"])
        if qty < info["min_qty"]:
            notifier.send(msg_error(self.name, coin, "Min Qty Hatasi", f"Hesaplanan qty {qty} < minQty {info['min_qty']}"))
            return

        # SL fiyati — giris fiyatinin %1'i ters yon
        if side == "long":
            sl_price = price * (1 - self.sl_pct)
            order_side = "Buy"
        else:
            sl_price = price * (1 + self.sl_pct)
            order_side = "Sell"
        sl_price = round_price(sl_price, info["tick_size"])

        # Kaldirac ayarla
        try:
            self.bybit.set_leverage(coin, self.leverage)
        except Exception as e:
            notifier.send(msg_error(self.name, coin, "Kaldirac Hatasi", str(e)))
            return

        # Emir gonder
        try:
            self.bybit.place_market_order(coin, order_side, qty, sl_price)
        except Exception as e:
            notifier.send(msg_error(self.name, coin, "Emir Hatasi", str(e)))
            return

        # Bybit'ten gercek entry price'i cek (slippage olabilir)
        # Kucuk bir gecikme — emrin yerlesmesi icin
        import time as _t
        _t.sleep(0.5)
        actual_entry_price = price
        try:
            bybit_pos = self.bybit.get_position(coin)
            if bybit_pos and bybit_pos["entry_price"] > 0:
                actual_entry_price = bybit_pos["entry_price"]
        except Exception:
            pass  # Hata olursa son ticker fiyatini kullan

        # SL fiyatini gercek entry'ye gore yeniden hesapla
        if side == "long":
            sl_price = actual_entry_price * (1 - self.sl_pct)
        else:
            sl_price = actual_entry_price * (1 + self.sl_pct)
        sl_price = round_price(sl_price, info["tick_size"])

        # Pozisyon kaydi
        entry_time = datetime.now()
        position = {
            "coin": coin,
            "color": color,
            "side": side,
            "entry_price": actual_entry_price,
            "entry_time": entry_time,
            "qty": qty,
            "volume": volume,
            "stake": stake,
            "leverage": self.leverage,
            "sl_price": sl_price,
            "level": "ENTRY",
            "highest_price": actual_entry_price,
            "lowest_price": actual_entry_price,
            "chandelier_start_price": None,
            "atr_at_entry": bands["atr"],
            "timeframe": self.timeframe,
            "order_side": order_side,
        }
        state.add_position(position)
        state.set_flag(coin, flag_name, False)  # giriste flag silinir
        state.flag_to_trade(coin, flag_name)

        # Yon degisimi olduysa once o bildirim, sonra islem acilis bildirimi
        if direction_changed and old_close_data and old_position:
            notifier.send(msg_direction_changed(
                old_position, color, side, old_close_data,
                state.get_open_count(), self.max_slots
            ))
        notifier.send(msg_trade_opened(position, state.get_open_count(), self.max_slots))
