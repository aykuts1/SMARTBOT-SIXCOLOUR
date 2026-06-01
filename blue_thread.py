"""
🔵 MAVİ THREAD (YENİ TASARIM — hedge izleyici)

Mavi, Kırmızı ekosisteminin HEDGE işlemleridir. Her PRIMARY için bir tane:
  - MAVİ    → Kırmızı'nın hedge'i      (slot HEDGE_MAIN,   parent = Kırmızı)
  - MAVİ 1  → Sarı 1'in hedge'i        (slot HEDGE_TREND1, parent = Sarı 1)
  - MAVİ 2  → Sarı 2'nin hedge'i       (slot HEDGE_TREND2, parent = Sarı 2)

Yön: parent'ın TERSİ (Kırmızı SHORT → Mavi LONG).

AÇILIŞ (konum bazlı, cross/EMA YOK):
  - Fiyat parent tablosunun LS1 bölgesine (LS1..LS3 dilimleri, LOSE'a doğru)
    girdiği anda Mavi açılır.
  - REENTRY VAR: parent yaşadıkça, Mavi kapanıp fiyat tekrar LS1'e girerse
    yeniden açılır.

ÇIKIŞ (Mavi için 3 yol):
  1) MUTLAK BAĞLILIK: parent kapandığı anda Mavi de kapanır.
  2) parent LOSE çizgisinin üstüne çıkış → kapanır (stop).
  3) parent LS TAMPON çizgisinin altına geçiş → KÂR ile kapanır (parent kalır).
"""
import threading
import logging

import geometry
from utils import crossed_up, crossed_down

log = logging.getLogger("BlueThread")


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


class BlueThread(threading.Thread):

    THREAD_COLOR = "BLUE"

    def __init__(self, config, data_manager, trade_manager):
        super().__init__(name="BlueThread", daemon=True)
        self.cfg = config
        self.dm = data_manager
        self.tm = trade_manager
        self._stop = threading.Event()
        self.tables = {}                 # primary_id -> HedgeTable
        self.tables_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    def get_open_flags(self):
        return []

    # ------------------------------------------------------------------
    # TABLO OLUŞTURMA (Red/Yellow çağırır)
    # ------------------------------------------------------------------
    def create_hedge_table(self, primary_trade, slot, label):
        if primary_trade is None or primary_trade.lz is None:
            return None
        table = HedgeTable(primary_trade, slot, label, self.THREAD_COLOR)
        with self.tables_lock:
            self.tables[primary_trade.id] = table
        return table

    # ------------------------------------------------------------------
    # SCAN
    # ------------------------------------------------------------------
    def scan(self):
        with self.tables_lock:
            tbls = list(self.tables.items())
        for primary_id, tbl in tbls:
            if self._stop.is_set():
                return
            try:
                self._tick_table(primary_id, tbl)
            except Exception as e:
                log.exception(f"BlueThread tick hatası ({tbl.symbol}): {e}")

    def _tick_table(self, primary_id, tbl):
        prev, curr = self.dm.get_price_pair(tbl.symbol)
        if prev is None or curr is None:
            return

        parent = self.tm.slots.get_trade_by_id(primary_id)

        # Aktif hedge kapandıysa (zincir/başka) state'i temizle
        if tbl.active_trade and tbl.active_trade.closed:
            tbl.active_trade = None

        # 1) MUTLAK BAĞLILIK: parent yoksa/kapalıysa
        if parent is None:
            if tbl.active_trade and not tbl.active_trade.closed:
                self.tm.close_trade(tbl.active_trade, f"{tbl.label} PARENT KAPANDI", curr)
            tbl.active_trade = None
            with self.tables_lock:
                self.tables.pop(primary_id, None)
            return

        # 2) AÇIK HEDGE VARSA → çıkış kontrolü
        if tbl.active_trade and not tbl.active_trade.closed:
            self._handle_active(tbl, prev, curr)
            return

        # 3) AÇIK HEDGE YOK → LS1 bölgesine girince aç
        zone = geometry.find_zone(tbl.parent_side, tbl.anchor, tbl.lz,
                                  curr, tbl.winrate_zones)
        if zone in ("LS1", "LS2", "LS3"):
            self._open_hedge(tbl, curr, parent)

    def _handle_active(self, tbl, prev, curr):
        trade = tbl.active_trade

        # LOSE üstüne çıkış (parent_side'a göre)
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

        # LS TAMPON altına geçiş → KÂR
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

    # ------------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------------
    def run(self):
        log.info("Mavi thread başladı.")
        scan_interval = self.cfg.thread_scan_interval_sec
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception as e:
                log.exception(f"BlueThread döngü hatası: {e}")
            self._stop.wait(scan_interval)
        log.info("Mavi thread durdu.")
