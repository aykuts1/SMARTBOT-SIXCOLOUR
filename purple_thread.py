"""
🟣 MOR THREAD (YENİ TASARIM — hedge izleyici)

Beyaz ekosisteminin HEDGE işlemleri. Mavi ile birebir aynı mantık.
  - MOR    → Beyaz'ın hedge'i       (HEDGE_MAIN,   parent = Beyaz)
  - MOR 1  → Turuncu 1'in hedge'i   (HEDGE_TREND1, parent = Turuncu 1)
  - MOR 2  → Turuncu 2'nin hedge'i  (HEDGE_TREND2, parent = Turuncu 2)

Yön: parent'ın tersi. Açılış: parent LS1 bölgesine girince (konum bazlı, reentry var).
Çıkış: parent kapandı / parent LOSE üstü / parent LS TAMPON altı (=KÂR).
"""
import threading
import logging

import geometry
from utils import crossed_up, crossed_down

log = logging.getLogger("PurpleThread")


class HedgeTable:
    __slots__ = ("primary_id", "top_id", "symbol",
                 "parent_side", "hedge_side", "anchor", "lz", "winrate_zones",
                 "lose_line", "ls_tampon_line", "slot", "label", "color",
                 "active_trade")

    def __init__(self, primary_trade, slot, label, color):
        self.primary_id = primary_trade.id
        self.top_id = primary_trade.extras.get("top_id", primary_trade.id)
        self.symbol = primary_trade.symbol
        self.parent_side = primary_trade.side
        self.hedge_side = "LONG" if primary_trade.side == "SHORT" else "SHORT"
        self.anchor = primary_trade.extras.get("anchor", primary_trade.entry_price)
        self.lz = primary_trade.lz
        self.winrate_zones = primary_trade.winrate_zones
        self.lose_line = primary_trade.level_lines.get("LOSE")
        self.ls_tampon_line = primary_trade.level_lines.get("LS_TAMPON")
        self.slot = slot
        self.label = label
        self.color = color
        self.active_trade = None


class PurpleThread(threading.Thread):

    THREAD_COLOR = "PURPLE"

    def __init__(self, config, data_manager, trade_manager):
        super().__init__(name="PurpleThread", daemon=True)
        self.cfg = config
        self.dm = data_manager
        self.tm = trade_manager
        self._stop = threading.Event()
        self.tables = {}
        self.tables_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    def get_open_flags(self):
        return []

    def create_hedge_table(self, primary_trade, slot, label):
        if primary_trade is None or primary_trade.lz is None:
            return None
        table = HedgeTable(primary_trade, slot, label, self.THREAD_COLOR)
        with self.tables_lock:
            self.tables[primary_trade.id] = table
        return table

    def scan(self):
        with self.tables_lock:
            tbls = list(self.tables.items())
        for primary_id, tbl in tbls:
            if self._stop.is_set():
                return
            try:
                self._tick_table(primary_id, tbl)
            except Exception as e:
                log.exception(f"PurpleThread tick hatası ({tbl.symbol}): {e}")

    def _tick_table(self, primary_id, tbl):
        prev, curr = self.dm.get_price_pair(tbl.symbol)
        if prev is None or curr is None:
            return

        parent = self.tm.slots.get_trade_by_id(primary_id)

        if tbl.active_trade and tbl.active_trade.closed:
            tbl.active_trade = None

        if parent is None:
            if tbl.active_trade and not tbl.active_trade.closed:
                self.tm.close_trade(tbl.active_trade, f"{tbl.label} PARENT KAPANDI", curr)
            tbl.active_trade = None
            with self.tables_lock:
                self.tables.pop(primary_id, None)
            return

        if tbl.active_trade and not tbl.active_trade.closed:
            self._handle_active(tbl, prev, curr)
            return

        zone = geometry.find_zone(tbl.parent_side, tbl.anchor, tbl.lz,
                                  curr, tbl.winrate_zones)
        if zone in ("LS1", "LS2", "LS3"):
            self._open_hedge(tbl, curr, parent)

    def _handle_active(self, tbl, prev, curr):
        trade = tbl.active_trade

        if tbl.lose_line is not None:
            if tbl.parent_side == "SHORT":
                if crossed_up(prev, curr, tbl.lose_line):
                    self.tm.close_trade(trade, f"{tbl.label} LOSE EXIT", curr)
                    tbl.active_trade = None
                    return
            else:
                if crossed_down(prev, curr, tbl.lose_line):
                    self.tm.close_trade(trade, f"{tbl.label} LOSE EXIT", curr)
                    tbl.active_trade = None
                    return

        if tbl.ls_tampon_line is not None:
            if tbl.parent_side == "SHORT":
                if crossed_down(prev, curr, tbl.ls_tampon_line):
                    self.tm.close_trade(trade, f"{tbl.label} LS TAMPON EXIT", curr)
                    tbl.active_trade = None
                    return
            else:
                if crossed_up(prev, curr, tbl.ls_tampon_line):
                    self.tm.close_trade(trade, f"{tbl.label} LS TAMPON EXIT", curr)
                    tbl.active_trade = None
                    return

    def _open_hedge(self, tbl, entry_price, parent):
        trade = self.tm.open_trade(
            symbol=tbl.symbol, side=tbl.hedge_side,
            thread=tbl.color, entry_price=entry_price,
            label=tbl.label, role="MEMBER",
            top_id=tbl.top_id, slot=tbl.slot, parent_id=tbl.primary_id,
            lose_line=tbl.lose_line, winrate_line=tbl.ls_tampon_line,
            level_lines=parent.level_lines, current_level=None,
        )
        if not trade:
            return False
        tbl.active_trade = trade
        log.info(f"[{tbl.symbol}] {tbl.label} {tbl.hedge_side} açıldı @ {entry_price}")
        return True

    def run(self):
        log.info("Mor thread başladı.")
        scan_interval = self.cfg.thread_scan_interval_sec
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception as e:
                log.exception(f"PurpleThread döngü hatası: {e}")
            self._stop.wait(scan_interval)
        log.info("Mor thread durdu.")
