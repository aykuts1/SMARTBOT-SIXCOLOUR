"""
🟡 SARI THREAD (YENİ TASARIM — 2 primary)

Sarı, Kırmızı ekosisteminin "trend pekiştirici" primary'leridir.
Kırmızı ile AYNI yön.

İki ayrı Sarı doğar (RedThread tarafından tetiklenir):
  - SARI 1 (slot TREND1): Kırmızı tablosunun ST1 bölgesine girince.
       Tablo çapası = Kırmızı ST1 çizgisi, winrate_zones = 4 (ENTRY + ST1..ST3).
       LOSE = Kırmızı giriş, WINRATE = Kırmızı WINRATE.
  - SARI 2 (slot TREND2): Kırmızı tablosunun ST2 bölgesine girince.
       Tablo çapası = Kırmızı ST2 çizgisi, winrate_zones = 3 (ENTRY + ST1..ST2).
       LOSE = Kırmızı ST1, WINRATE = Kırmızı WINRATE.

Her Sarı:
  - Kendi tablosunu + kendi chandelier'ini (mesafe = LZ) taşır.
  - Açılınca kendi hedge'ini (Mavi 1 / Mavi 2) doğurur (BlueThread).
  - REENTRY YOK. Kapanınca tekrar açılmaz (Kırmızı tekrar doğurmaz).

Çıkışlar (üçü de Sarı + kendi Mavi'sini kapatır; Kırmızı'ya dokunmaz):
  1) WINRATE cross → KÂR
  2) Chandelier ters cross → trailing stop
  3) LOSE üstüne çıkış → stop loss

Kırmızı kapanırsa → ekosistem zinciri Sarı'yı da kapatır (close_ecosystem).
"""
import threading
import logging

import geometry
from trade_manager import hedge_slot_for, SLOT_TREND1
from utils import crossed_up, crossed_down

log = logging.getLogger("YellowThread")


class YellowThread(threading.Thread):

    THREAD_COLOR = "YELLOW"
    HEDGE_LABEL = {"TREND1": "MAVİ 1", "TREND2": "MAVİ 2"}

    def __init__(self, config, data_manager, trade_manager, blue_thread_ref=None):
        super().__init__(name="YellowThread", daemon=True)
        self.cfg = config
        self.dm = data_manager
        self.tm = trade_manager
        self.blue = blue_thread_ref
        self._stop = threading.Event()

    def set_blue_ref(self, blue):
        self.blue = blue

    def stop(self):
        self._stop.set()

    def get_open_flags(self):
        return []

    # ------------------------------------------------------------------
    # DOĞURMA (RedThread çağırır)
    # ------------------------------------------------------------------
    def spawn_sari(self, red_trade, slot, label, winrate_zones):
        """
        red_trade: Kırmızı (top) trade.
        slot: TREND1 / TREND2
        label: "SARI 1" / "SARI 2"
        winrate_zones: 4 (Sarı1) / 3 (Sarı2)
        """
        if red_trade is None or red_trade.closed:
            return False
        lz = red_trade.lz
        if lz is None or lz <= 0:
            return False

        # Çapa çizgisi: Kırmızı'nın ilgili ST çizgisi
        anchor_name = "ST1" if slot == SLOT_TREND1 else "ST2"
        anchor = red_trade.level_lines.get(anchor_name)
        if anchor is None:
            return False

        lines = geometry.build_table(red_trade.side, anchor, lz, winrate_zones)

        curr = self.dm.get_last_price(red_trade.symbol)
        if curr is None:
            return False

        trade = self.tm.open_trade(
            symbol=red_trade.symbol, side=red_trade.side,
            thread=self.THREAD_COLOR, entry_price=curr,
            label=label, role="MEMBER",
            top_id=red_trade.id, slot=slot, parent_id=red_trade.id,
            lose_line=lines["LOSE"], winrate_line=lines["WINRATE"],
            level_lines=lines, current_level="ENTRY",
        )
        if not trade:
            return False

        trade.lz = lz
        trade.winrate_zones = winrate_zones
        # Tablo çapası (geometri ENTRY) gerçek fill fiyatından farklı olabilir;
        # seviye/çizgi hesapları çapaya, PnL gerçek entry'ye göre yapılır.
        trade.extras["anchor"] = anchor
        trade.chandelier_distance = lz
        trade.chandelier_best_price = curr
        if red_trade.side == "SHORT":
            trade.chandelier_line = curr + lz
        else:
            trade.chandelier_line = curr - lz

        # Hedge (Mavi 1 / Mavi 2)
        try:
            if self.blue:
                hedge_label = self.HEDGE_LABEL.get(slot, "MAVİ")
                self.blue.create_hedge_table(trade, hedge_slot_for(slot), hedge_label)
        except Exception as e:
            log.error(f"Sarı hedge tablo hatası: {e}")

        log.info(f"[{red_trade.symbol}] {label} {red_trade.side} doğdu @ {curr}")
        return True

    # ------------------------------------------------------------------
    # SCAN — açık Sarı'ları yönet
    # ------------------------------------------------------------------
    def scan(self):
        trades = self.tm.slots.get_open_by_thread(self.THREAD_COLOR)
        for t in trades:
            if self._stop.is_set():
                return
            try:
                self._tick_sari(t)
            except Exception as e:
                log.exception(f"YellowThread tick hatası ({t.symbol}): {e}")

    def _tick_sari(self, trade):
        prev, curr = self.dm.get_price_pair(trade.symbol)
        if prev is None or curr is None:
            return
        # Kurulum (lz/chandelier) henüz tamamlanmadıysa bu tick'i atla (yarış koruması)
        if trade.lz is None:
            return

        anchor = trade.extras.get("anchor", trade.entry_price)
        levels = trade.level_lines

        # Chandelier güncelle
        self._update_chandelier(trade, curr)

        # 1) WINRATE çıkışı
        if trade.side == "SHORT":
            if crossed_down(prev, curr, levels["WINRATE"]):
                self.tm.close_primary_and_hedge(trade, f"{trade.label} WINRATE EXIT", curr)
                return
        else:
            if crossed_up(prev, curr, levels["WINRATE"]):
                self.tm.close_primary_and_hedge(trade, f"{trade.label} WINRATE EXIT", curr)
                return

        # 2) Chandelier çıkışı
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

        # 3) LOSE çıkışı
        if trade.side == "SHORT":
            if crossed_up(prev, curr, levels["LOSE"]):
                self.tm.close_primary_and_hedge(trade, f"{trade.label} LOSE EXIT", curr)
                return
        else:
            if crossed_down(prev, curr, levels["LOSE"]):
                self.tm.close_primary_and_hedge(trade, f"{trade.label} LOSE EXIT", curr)
                return

        # 4) Seviye telemetrisi (çapaya göre, sadece ileri yönlü bildirim)
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
        log.info("Sarı thread başladı.")
        scan_interval = self.cfg.thread_scan_interval_sec
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception as e:
                log.exception(f"YellowThread döngü hatası: {e}")
            self._stop.wait(scan_interval)
        log.info("Sarı thread durdu.")
