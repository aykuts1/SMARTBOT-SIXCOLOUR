"""
🔴 KIRMIZI THREAD (YENİ TASARIM)

AÇILIŞ MANTIĞI (DEĞİŞMEDİ):
1) FLAG (15dk mum kapanışında)
   - Donchian alt çizgisi önceki kapanıştan yukarı → SHORT flag
   - Donchian üst çizgisi önceki kapanıştan aşağı → LONG flag
2) GİRİŞ ÇİZGİSİ KAYDI (değme)
   - Fiyat Donchian çizgisine değince o anki Donchian değeri giriş çizgisi olarak kaydedilir
3) İŞLEM AÇILIŞI (statik çizgi cross + EMA800)
   - SHORT: çizgiyi aşağı cross + fiyat < EMA800
   - LONG:  çizgiyi yukarı cross + fiyat > EMA800
4) Değme ve cross aynı taramada üst üste binmez

TABLO (YENİ GEOMETRİ — geometry.py):
   LOSE ZONE = max %2. LS dilimleri + LS TAMPON + ENTRY/ST1..ST4 + WINRATE(=E-5·LZ).

EKOSİSTEM (açılışta kurulur):
   - Mavi (hedge)  → BlueThread, Kırmızı tablosunun LS1 bölgesine girince açılır
   - Sarı 1        → Kırmızı tablosunun ST1 bölgesine girince doğar
   - Sarı 2        → Kırmızı tablosunun ST2 bölgesine girince doğar

ÇIKIŞLAR (Kırmızı için, üçü de ekosistemin TAMAMINI kapatır):
   1) WINRATE cross → KÂR
   2) Chandelier (mesafe = LZ) ters cross → trailing stop
   3) LOSE üstüne çıkış → stop loss
   Reentry YOK.
"""
import threading
import logging

import geometry
from trade_manager import SLOT_TREND1, SLOT_TREND2, SLOT_HEDGE_MAIN
from utils import crossed_up, crossed_down

log = logging.getLogger("RedThread")

WINRATE_ZONES = 5  # Kırmızı/Beyaz: ST1..ST4 + WINRATE


class RedThread(threading.Thread):

    def __init__(self, config, data_manager, trade_manager,
                 blue_thread_ref=None, yellow_thread_ref=None):
        super().__init__(name="RedThread", daemon=True)
        self.cfg = config
        self.dm = data_manager
        self.tm = trade_manager
        self.blue = blue_thread_ref
        self.yellow = yellow_thread_ref

        self._stop = threading.Event()

        self.state = {s: {
            "long_flag": False,
            "short_flag": False,
            "long_entry_line": None,
            "short_entry_line": None,
        } for s in config.symbols}

        self.last_flag_check_ts = {s: None for s in config.symbols}

    def set_thread_refs(self, blue, yellow):
        self.blue = blue
        self.yellow = yellow

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    # RAPOR HELPER
    # ------------------------------------------------------------------
    def get_open_flags(self):
        result = []
        for symbol, st in self.state.items():
            if st["long_flag"]:
                result.append({"symbol": symbol, "thread": "RED", "side": "LONG",
                               "entry_line": st["long_entry_line"]})
            if st["short_flag"]:
                result.append({"symbol": symbol, "thread": "RED", "side": "SHORT",
                               "entry_line": st["short_entry_line"]})
        return result

    # ------------------------------------------------------------------
    # FLAG TARAMA — 15dk mum kapanışında (DEĞİŞMEDİ)
    # ------------------------------------------------------------------
    def scan_flags(self):
        for symbol in self.cfg.symbols:
            if self.dm.is_paused(symbol):
                continue
            if self._stop.is_set():
                return
            self._scan_flag_one(symbol)

    def _scan_flag_one(self, symbol):
        snap = self.dm.get_snapshot(symbol)
        if not snap:
            return
        upper_hist = snap["donchian_upper_history"]
        lower_hist = snap["donchian_lower_history"]
        if len(upper_hist) < 2 or upper_hist[-1] is None or upper_hist[-2] is None:
            return
        if lower_hist[-1] is None or lower_hist[-2] is None:
            return

        cur_upper = upper_hist[-1]
        prev_upper = upper_hist[-2]
        cur_lower = lower_hist[-1]
        prev_lower = lower_hist[-2]

        last_close_ts = snap["last_candle_close_ts"]
        if self.last_flag_check_ts.get(symbol) == last_close_ts:
            return
        self.last_flag_check_ts[symbol] = last_close_ts

        st = self.state[symbol]

        if cur_lower > prev_lower:
            if not st["short_flag"]:
                st["short_flag"] = True
                st["short_entry_line"] = None
                self.tm.log_flag_event(symbol, "RED", "SHORT", "OPENED")

        if cur_upper < prev_upper:
            if not st["long_flag"]:
                st["long_flag"] = True
                st["long_entry_line"] = None
                self.tm.log_flag_event(symbol, "RED", "LONG", "OPENED")

    # ------------------------------------------------------------------
    # AÇILIŞ (DEĞİŞMEDİ — sadece _open_red yeni tabloyu kurar)
    # ------------------------------------------------------------------
    def scan_open_signals(self):
        for symbol in self.cfg.symbols:
            if self.dm.is_paused(symbol):
                continue
            if self._stop.is_set():
                return
            self._scan_open_one(symbol)

    def _scan_open_one(self, symbol):
        prev, curr = self.dm.get_price_pair(symbol)
        if prev is None or curr is None:
            return

        d_upper, d_lower = self.dm.get_donchian_current(symbol)
        ema_val = self.dm.get_ema(symbol)
        if d_upper is None or d_lower is None or ema_val is None:
            return

        st = self.state[symbol]

        if st["short_flag"]:
            self._tick_short_open(symbol, st, prev, curr, d_lower, d_upper, ema_val)
        if st["long_flag"]:
            self._tick_long_open(symbol, st, prev, curr, d_upper, d_lower, ema_val)

    def _tick_short_open(self, symbol, st, prev, curr, d_lower, d_upper, ema_val):
        if crossed_down(prev, curr, d_lower):
            st["short_entry_line"] = d_lower
            log.info(f"[{symbol}] SHORT giriş çizgisi kaydedildi: {d_lower}")
            return
        if st["short_entry_line"] is not None:
            entry_line = st["short_entry_line"]
            if crossed_down(prev, curr, entry_line):
                if curr < ema_val:
                    self._open_red(symbol, "SHORT", curr, d_upper, d_lower)

    def _tick_long_open(self, symbol, st, prev, curr, d_upper, d_lower, ema_val):
        if crossed_up(prev, curr, d_upper):
            st["long_entry_line"] = d_upper
            log.info(f"[{symbol}] LONG giriş çizgisi kaydedildi: {d_upper}")
            return
        if st["long_entry_line"] is not None:
            entry_line = st["long_entry_line"]
            if crossed_up(prev, curr, entry_line):
                if curr > ema_val:
                    self._open_red(symbol, "LONG", curr, d_upper, d_lower)

    # ------------------------------------------------------------------
    # KIRMIZI AÇ + TABLO + EKOSİSTEM
    # ------------------------------------------------------------------
    def _open_red(self, symbol, side, entry_price, d_upper, d_lower):
        donch = d_upper if side == "SHORT" else d_lower
        lz = geometry.compute_lose_zone(side, entry_price, donch, self.cfg.max_lose_pct)
        if lz is None or lz <= 0:
            return
        lines = geometry.build_table(side, entry_price, lz, WINRATE_ZONES)

        trade = self.tm.open_trade(
            symbol=symbol, side=side, thread="RED", entry_price=entry_price,
            label="KIRMIZI", role="TOP",
            lose_line=lines["LOSE"], winrate_line=lines["WINRATE"],
            level_lines=lines, current_level="ENTRY",
        )
        if not trade:
            return

        # Geometri + chandelier
        trade.lz = lz
        trade.winrate_zones = WINRATE_ZONES
        trade.chandelier_distance = lz
        trade.chandelier_best_price = entry_price
        trade.chandelier_line = lines["LOSE"]  # = entry + lz (SHORT)
        trade.extras["sari1_spawned"] = False
        trade.extras["sari2_spawned"] = False

        # Flag + giriş çizgisi temizle
        st = self.state[symbol]
        if side == "SHORT":
            if st["short_flag"]:
                st["short_flag"] = False
                self.tm.log_flag_event(symbol, "RED", "SHORT", "CONVERTED")
            st["short_entry_line"] = None
        else:
            if st["long_flag"]:
                st["long_flag"] = False
                self.tm.log_flag_event(symbol, "RED", "LONG", "CONVERTED")
            st["long_entry_line"] = None

        # Mavi (hedge) tablosunu kur
        try:
            if self.blue:
                self.blue.create_hedge_table(trade, SLOT_HEDGE_MAIN, "MAVİ")
        except Exception as e:
            log.error(f"Mavi hedge tablo hatası: {e}")

    # ------------------------------------------------------------------
    # SEVİYE / ÇIKIŞ / SARI DOĞURMA
    # ------------------------------------------------------------------
    def scan_levels_and_exits(self):
        trades = self.tm.slots.get_open_by_thread("RED")
        for t in trades:
            if self._stop.is_set():
                return
            try:
                self._tick_red(t)
            except Exception as e:
                log.exception(f"RedThread tick hatası ({t.symbol}): {e}")

    def _tick_red(self, trade):
        prev, curr = self.dm.get_price_pair(trade.symbol)
        if prev is None or curr is None:
            return
        if trade.lz is None:
            return

        levels = trade.level_lines

        # Chandelier güncelle
        self._update_chandelier(trade, curr)

        # 1) WINRATE çıkışı
        if trade.side == "SHORT":
            if crossed_down(prev, curr, levels["WINRATE"]):
                self.tm.close_ecosystem(trade, "KIRMIZI WINRATE EXIT", curr)
                return
        else:
            if crossed_up(prev, curr, levels["WINRATE"]):
                self.tm.close_ecosystem(trade, "KIRMIZI WINRATE EXIT", curr)
                return

        # 2) Chandelier çıkışı
        cl = trade.chandelier_line
        if cl is not None:
            if trade.side == "SHORT":
                if crossed_up(prev, curr, cl):
                    self.tm.close_ecosystem(trade, "KIRMIZI CHANDELIER EXIT", curr)
                    return
            else:
                if crossed_down(prev, curr, cl):
                    self.tm.close_ecosystem(trade, "KIRMIZI CHANDELIER EXIT", curr)
                    return

        # 3) LOSE çıkışı
        if trade.side == "SHORT":
            if crossed_up(prev, curr, levels["LOSE"]):
                self.tm.close_ecosystem(trade, "KIRMIZI LOSE EXIT", curr)
                return
        else:
            if crossed_down(prev, curr, levels["LOSE"]):
                self.tm.close_ecosystem(trade, "KIRMIZI LOSE EXIT", curr)
                return

        # 4) Sarı doğurma (ST1 → Sarı1, ST2 → Sarı2)
        self._maybe_spawn_sari(trade, curr)

        # 5) Seviye telemetrisi
        self._update_level(trade, curr)

    def _update_chandelier(self, trade, curr):
        if trade.chandelier_distance is None:
            trade.chandelier_distance = trade.lz
        if trade.chandelier_best_price is None:
            trade.chandelier_best_price = curr
        else:
            if trade.side == "SHORT":
                if curr < trade.chandelier_best_price:
                    trade.chandelier_best_price = curr
            else:
                if curr > trade.chandelier_best_price:
                    trade.chandelier_best_price = curr
        if trade.side == "SHORT":
            trade.chandelier_line = trade.chandelier_best_price + trade.chandelier_distance
        else:
            trade.chandelier_line = trade.chandelier_best_price - trade.chandelier_distance

    def _maybe_spawn_sari(self, trade, curr):
        p = geometry.zone_position(trade.side, trade.entry_price, trade.lz, curr)
        if p is None:
            return
        # Sarı 1: fiyat ST1 çizgisine ulaştı (p <= -1)
        if not trade.extras.get("sari1_spawned") and p <= -1.0:
            ok = False
            try:
                if self.yellow:
                    ok = self.yellow.spawn_sari(trade, SLOT_TREND1, "SARI 1", 4)
            except Exception as e:
                log.error(f"Sarı1 doğurma hatası: {e}")
            if ok:
                trade.extras["sari1_spawned"] = True
        # Sarı 2: fiyat ST2 çizgisine ulaştı (p <= -2)
        if not trade.extras.get("sari2_spawned") and p <= -2.0:
            ok = False
            try:
                if self.yellow:
                    ok = self.yellow.spawn_sari(trade, SLOT_TREND2, "SARI 2", 3)
            except Exception as e:
                log.error(f"Sarı2 doğurma hatası: {e}")
            if ok:
                trade.extras["sari2_spawned"] = True

    def _update_level(self, trade, curr):
        zone = geometry.find_zone(trade.side, trade.entry_price, trade.lz,
                                  curr, trade.winrate_zones)
        if zone and zone != trade.current_level:
            trade.current_level = zone
            # Bildirim SADECE ileri yönlü (daha derin ST) atılır
            if zone.startswith("ST"):
                try:
                    k = int(zone[2:])
                except ValueError:
                    k = 0
                if k > trade.extras.get("max_st", 0):
                    trade.extras["max_st"] = k
                    trade.highest_level = zone
                    self.tm.tg.notify_level_change(trade, zone)

    # ------------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------------
    def run(self):
        log.info("Kırmızı thread başladı.")
        scan_interval = self.cfg.thread_scan_interval_sec
        while not self._stop.is_set():
            try:
                self.scan_open_signals()
                self.scan_levels_and_exits()
            except Exception as e:
                log.exception(f"RedThread döngü hatası: {e}")
            self._stop.wait(scan_interval)
        log.info("Kırmızı thread durdu.")
