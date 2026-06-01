"""
⚪️ BEYAZ THREAD (YENİ TASARIM)

Kırmızı'dan bağımsız ikinci ana thread.

AÇILIŞ MANTIĞI (DEĞİŞMEDİ — anlık değme, 15dk beklemez):
1) Fiyat Donchian50 üst/alt çizgisine değer → flag + giriş çizgisi kaydı.
   - Mesafe = (Donchian üst - alt) / 4
   - SHORT giriş çizgisi = Donchian üst - mesafe (Donchian'ın 1/4 içinde)
   - LONG  giriş çizgisi = Donchian alt + mesafe
   - Fiyat tekrar değdiğinde giriş çizgisi güncellenir, flag silinmez.
2) Giriş çizgisini cross + EMA800 → BEYAZ açılır.
3) Değme ve cross aynı taramada üst üste binmez.

TABLO (YENİ GEOMETRİ — geometry.py, Kırmızı ile AYNI, %2 sınırı dahil):
   LOSE ZONE = max %2. WINRATE = E - 5·LZ.

EKOSİSTEM:
   - Mor (hedge)   → PurpleThread, Beyaz tablosunun LS1 bölgesine girince
   - Turuncu 1     → Beyaz tablosunun ST1 bölgesine girince doğar
   - Turuncu 2     → Beyaz tablosunun ST2 bölgesine girince doğar

ÇIKIŞLAR (ekosistemin tamamını kapatır): WINRATE / Chandelier(LZ) / LOSE.
"""
import threading
import logging

import geometry
from trade_manager import SLOT_TREND1, SLOT_TREND2, SLOT_HEDGE_MAIN
from utils import crossed_up, crossed_down

log = logging.getLogger("WhiteThread")

WINRATE_ZONES = 5


class WhiteThread(threading.Thread):

    def __init__(self, config, data_manager, trade_manager,
                 purple_thread_ref=None, orange_thread_ref=None):
        super().__init__(name="WhiteThread", daemon=True)
        self.cfg = config
        self.dm = data_manager
        self.tm = trade_manager
        self.purple = purple_thread_ref
        self.orange = orange_thread_ref

        self._stop = threading.Event()

        self.state = {s: {
            "long_flag": False,
            "short_flag": False,
            "long_entry_line": None,
            "short_entry_line": None,
        } for s in config.symbols}

    def set_thread_refs(self, purple, orange):
        self.purple = purple
        self.orange = orange

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    # RAPOR HELPER
    # ------------------------------------------------------------------
    def get_open_flags(self):
        result = []
        for symbol, st in self.state.items():
            if st["long_flag"]:
                result.append({"symbol": symbol, "thread": "WHITE", "side": "LONG",
                               "entry_line": st["long_entry_line"]})
            if st["short_flag"]:
                result.append({"symbol": symbol, "thread": "WHITE", "side": "SHORT",
                               "entry_line": st["short_entry_line"]})
        return result

    # ------------------------------------------------------------------
    # AÇILIŞ (DEĞİŞMEDİ)
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
        self._tick_short_open(symbol, st, prev, curr, d_upper, d_lower, ema_val)
        self._tick_long_open(symbol, st, prev, curr, d_upper, d_lower, ema_val)

    def _calc_entry_line(self, d_upper, d_lower, side):
        if d_upper is None or d_lower is None:
            return None
        spread = d_upper - d_lower
        if spread <= 0:
            return None
        mesafe = spread / 4.0
        if side == "SHORT":
            return d_upper - mesafe
        else:
            return d_lower + mesafe

    def _tick_short_open(self, symbol, st, prev, curr, d_upper, d_lower, ema_val):
        if crossed_up(prev, curr, d_upper):
            new_entry = self._calc_entry_line(d_upper, d_lower, "SHORT")
            if new_entry is None:
                return
            if not st["short_flag"]:
                st["short_flag"] = True
                self.tm.log_flag_event(symbol, "WHITE", "SHORT", "OPENED")
            st["short_entry_line"] = new_entry
            log.info(f"[{symbol}] BEYAZ SHORT giriş çizgisi: {new_entry}")
            return
        if st["short_flag"] and st["short_entry_line"] is not None:
            entry_line = st["short_entry_line"]
            if crossed_down(prev, curr, entry_line):
                if curr < ema_val:
                    self._open_white(symbol, "SHORT", entry_line, d_upper, d_lower)

    def _tick_long_open(self, symbol, st, prev, curr, d_upper, d_lower, ema_val):
        if crossed_down(prev, curr, d_lower):
            new_entry = self._calc_entry_line(d_upper, d_lower, "LONG")
            if new_entry is None:
                return
            if not st["long_flag"]:
                st["long_flag"] = True
                self.tm.log_flag_event(symbol, "WHITE", "LONG", "OPENED")
            st["long_entry_line"] = new_entry
            log.info(f"[{symbol}] BEYAZ LONG giriş çizgisi: {new_entry}")
            return
        if st["long_flag"] and st["long_entry_line"] is not None:
            entry_line = st["long_entry_line"]
            if crossed_up(prev, curr, entry_line):
                if curr > ema_val:
                    self._open_white(symbol, "LONG", entry_line, d_upper, d_lower)

    # ------------------------------------------------------------------
    # BEYAZ AÇ + TABLO + EKOSİSTEM
    # ------------------------------------------------------------------
    def _open_white(self, symbol, side, entry_price, d_upper, d_lower):
        donch = d_upper if side == "SHORT" else d_lower
        lz = geometry.compute_lose_zone(side, entry_price, donch, self.cfg.max_lose_pct)
        if lz is None or lz <= 0:
            return
        lines = geometry.build_table(side, entry_price, lz, WINRATE_ZONES)

        trade = self.tm.open_trade(
            symbol=symbol, side=side, thread="WHITE", entry_price=entry_price,
            label="BEYAZ", role="TOP",
            lose_line=lines["LOSE"], winrate_line=lines["WINRATE"],
            level_lines=lines, current_level="ENTRY",
        )
        if not trade:
            return

        trade.lz = lz
        trade.winrate_zones = WINRATE_ZONES
        trade.chandelier_distance = lz
        trade.chandelier_best_price = entry_price
        trade.chandelier_line = lines["LOSE"]
        trade.extras["sari1_spawned"] = False
        trade.extras["sari2_spawned"] = False

        st = self.state[symbol]
        if side == "SHORT":
            if st["short_flag"]:
                st["short_flag"] = False
                self.tm.log_flag_event(symbol, "WHITE", "SHORT", "CONVERTED")
            st["short_entry_line"] = None
        else:
            if st["long_flag"]:
                st["long_flag"] = False
                self.tm.log_flag_event(symbol, "WHITE", "LONG", "CONVERTED")
            st["long_entry_line"] = None

        try:
            if self.purple:
                self.purple.create_hedge_table(trade, SLOT_HEDGE_MAIN, "MOR")
        except Exception as e:
            log.error(f"Mor hedge tablo hatası: {e}")

    # ------------------------------------------------------------------
    # SEVİYE / ÇIKIŞ / TURUNCU DOĞURMA
    # ------------------------------------------------------------------
    def scan_levels_and_exits(self):
        trades = self.tm.slots.get_open_by_thread("WHITE")
        for t in trades:
            if self._stop.is_set():
                return
            try:
                self._tick_white(t)
            except Exception as e:
                log.exception(f"WhiteThread tick hatası ({t.symbol}): {e}")

    def _tick_white(self, trade):
        prev, curr = self.dm.get_price_pair(trade.symbol)
        if prev is None or curr is None:
            return
        if trade.lz is None:
            return

        levels = trade.level_lines
        self._update_chandelier(trade, curr)

        # 1) WINRATE
        if trade.side == "SHORT":
            if crossed_down(prev, curr, levels["WINRATE"]):
                self.tm.close_ecosystem(trade, "BEYAZ WINRATE EXIT", curr)
                return
        else:
            if crossed_up(prev, curr, levels["WINRATE"]):
                self.tm.close_ecosystem(trade, "BEYAZ WINRATE EXIT", curr)
                return

        # 2) Chandelier
        cl = trade.chandelier_line
        if cl is not None:
            if trade.side == "SHORT":
                if crossed_up(prev, curr, cl):
                    self.tm.close_ecosystem(trade, "BEYAZ CHANDELIER EXIT", curr)
                    return
            else:
                if crossed_down(prev, curr, cl):
                    self.tm.close_ecosystem(trade, "BEYAZ CHANDELIER EXIT", curr)
                    return

        # 3) LOSE
        if trade.side == "SHORT":
            if crossed_up(prev, curr, levels["LOSE"]):
                self.tm.close_ecosystem(trade, "BEYAZ LOSE EXIT", curr)
                return
        else:
            if crossed_down(prev, curr, levels["LOSE"]):
                self.tm.close_ecosystem(trade, "BEYAZ LOSE EXIT", curr)
                return

        # 4) Turuncu doğurma
        self._maybe_spawn_turuncu(trade, curr)

        # 5) Seviye telemetrisi (sadece ileri yönlü bildirim)
        zone = geometry.find_zone(trade.side, trade.entry_price, trade.lz,
                                  curr, trade.winrate_zones)
        if zone and zone != trade.current_level:
            trade.current_level = zone
            if zone.startswith("ST"):
                try:
                    k = int(zone[2:])
                except ValueError:
                    k = 0
                if k > trade.extras.get("max_st", 0):
                    trade.extras["max_st"] = k
                    trade.highest_level = zone
                    self.tm.tg.notify_level_change(trade, zone)

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

    def _maybe_spawn_turuncu(self, trade, curr):
        p = geometry.zone_position(trade.side, trade.entry_price, trade.lz, curr)
        if p is None:
            return
        if not trade.extras.get("sari1_spawned") and p <= -1.0:
            ok = False
            try:
                if self.orange:
                    ok = self.orange.spawn_turuncu(trade, SLOT_TREND1, "TURUNCU 1", 4)
            except Exception as e:
                log.error(f"Turuncu1 doğurma hatası: {e}")
            if ok:
                trade.extras["sari1_spawned"] = True
        if not trade.extras.get("sari2_spawned") and p <= -2.0:
            ok = False
            try:
                if self.orange:
                    ok = self.orange.spawn_turuncu(trade, SLOT_TREND2, "TURUNCU 2", 3)
            except Exception as e:
                log.error(f"Turuncu2 doğurma hatası: {e}")
            if ok:
                trade.extras["sari2_spawned"] = True

    # ------------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------------
    def run(self):
        log.info("Beyaz thread başladı.")
        scan_interval = self.cfg.thread_scan_interval_sec
        while not self._stop.is_set():
            try:
                self.scan_open_signals()
                self.scan_levels_and_exits()
            except Exception as e:
                log.exception(f"WhiteThread döngü hatası: {e}")
            self._stop.wait(scan_interval)
        log.info("Beyaz thread durdu.")
