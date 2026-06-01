"""
🟠 TURUNCU THREAD (YENİ TASARIM — 2 primary)

Beyaz ekosisteminin trend pekiştirici primary'leri. Sarı ile birebir aynı mantık.
  - TURUNCU 1 (TREND1): Beyaz ST1 bölgesine girince, winrate_zones = 4.
  - TURUNCU 2 (TREND2): Beyaz ST2 bölgesine girince, winrate_zones = 3.

Her Turuncu kendi tablosu + chandelier'i (mesafe = LZ) + kendi hedge'i (Mor 1/2).
REENTRY YOK. Çıkışlar: WINRATE / Chandelier / LOSE (Turuncu + kendi Mor'u kapanır).
"""
import threading
import logging

import geometry
from trade_manager import hedge_slot_for, SLOT_TREND1
from utils import crossed_up, crossed_down

log = logging.getLogger("OrangeThread")


class OrangeThread(threading.Thread):

    THREAD_COLOR = "ORANGE"
    HEDGE_LABEL = {"TREND1": "MOR 1", "TREND2": "MOR 2"}

    def __init__(self, config, data_manager, trade_manager, purple_thread_ref=None):
        super().__init__(name="OrangeThread", daemon=True)
        self.cfg = config
        self.dm = data_manager
        self.tm = trade_manager
        self.purple = purple_thread_ref
        self._stop = threading.Event()

    def set_purple_ref(self, purple):
        self.purple = purple

    def stop(self):
        self._stop.set()

    def get_open_flags(self):
        return []

    # ------------------------------------------------------------------
    # DOĞURMA (WhiteThread çağırır)
    # ------------------------------------------------------------------
    def spawn_turuncu(self, white_trade, slot, label, winrate_zones):
        if white_trade is None or white_trade.closed:
            return False
        lz = white_trade.lz
        if lz is None or lz <= 0:
            return False

        anchor_name = "ST1" if slot == SLOT_TREND1 else "ST2"
        anchor = white_trade.level_lines.get(anchor_name)
        if anchor is None:
            return False

        lines = geometry.build_table(white_trade.side, anchor, lz, winrate_zones)

        curr = self.dm.get_last_price(white_trade.symbol)
        if curr is None:
            return False

        trade = self.tm.open_trade(
            symbol=white_trade.symbol, side=white_trade.side,
            thread=self.THREAD_COLOR, entry_price=curr,
            label=label, role="MEMBER",
            top_id=white_trade.id, slot=slot, parent_id=white_trade.id,
            lose_line=lines["LOSE"], winrate_line=lines["WINRATE"],
            level_lines=lines, current_level="ENTRY",
        )
        if not trade:
            return False

        trade.lz = lz
        trade.winrate_zones = winrate_zones
        trade.extras["anchor"] = anchor
        trade.chandelier_distance = lz
        trade.chandelier_best_price = curr
        if white_trade.side == "SHORT":
            trade.chandelier_line = curr + lz
        else:
            trade.chandelier_line = curr - lz

        try:
            if self.purple:
                hedge_label = self.HEDGE_LABEL.get(slot, "MOR")
                self.purple.create_hedge_table(trade, hedge_slot_for(slot), hedge_label)
        except Exception as e:
            log.error(f"Turuncu hedge tablo hatası: {e}")

        log.info(f"[{white_trade.symbol}] {label} {white_trade.side} doğdu @ {curr}")
        return True

    # ------------------------------------------------------------------
    # SCAN
    # ------------------------------------------------------------------
    def scan(self):
        trades = self.tm.slots.get_open_by_thread(self.THREAD_COLOR)
        for t in trades:
            if self._stop.is_set():
                return
            try:
                self._tick_turuncu(t)
            except Exception as e:
                log.exception(f"OrangeThread tick hatası ({t.symbol}): {e}")

    def _tick_turuncu(self, trade):
        prev, curr = self.dm.get_price_pair(trade.symbol)
        if prev is None or curr is None:
            return
        # Kurulum (lz/chandelier) henüz tamamlanmadıysa bu tick'i atla (yarış koruması)
        if trade.lz is None:
            return

        anchor = trade.extras.get("anchor", trade.entry_price)
        levels = trade.level_lines
        self._update_chandelier(trade, curr)

        if trade.side == "SHORT":
            if crossed_down(prev, curr, levels["WINRATE"]):
                self.tm.close_primary_and_hedge(trade, f"{trade.label} WINRATE EXIT", curr)
                return
        else:
            if crossed_up(prev, curr, levels["WINRATE"]):
                self.tm.close_primary_and_hedge(trade, f"{trade.label} WINRATE EXIT", curr)
                return

        cl = trade.chandelier_line
        if cl is not None:
            if trade.side == "SHORT":
                if crossed_up(prev, curr, cl):
                    self.tm.close_primary_and_hedge(trade, f"{trade.label} CHANDELIER EXIT", curr)
                    return
            else:
                if crossed_down(prev, curr, cl):
                    self.tm.close_primary_and_hedge(trade, f"{trade.label} CHANDELIER EXIT", curr)
                    return

        if trade.side == "SHORT":
            if crossed_up(prev, curr, levels["LOSE"]):
                self.tm.close_primary_and_hedge(trade, f"{trade.label} LOSE EXIT", curr)
                return
        else:
            if crossed_down(prev, curr, levels["LOSE"]):
                self.tm.close_primary_and_hedge(trade, f"{trade.label} LOSE EXIT", curr)
                return

        zone = geometry.find_zone(trade.side, anchor, trade.lz, curr, trade.winrate_zones)
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

    # ------------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------------
    def run(self):
        log.info("Turuncu thread başladı.")
        scan_interval = self.cfg.thread_scan_interval_sec
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception as e:
                log.exception(f"OrangeThread döngü hatası: {e}")
            self._stop.wait(scan_interval)
        log.info("Turuncu thread durdu.")
