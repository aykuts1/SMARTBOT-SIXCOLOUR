"""
🔵 MAVİ THREAD — YENİ TASARIM

Yön:
  Kırmızı Short → Mavi Long
  Kırmızı Long  → Mavi Short

Tablo (Mavi Long, Kırmızı Short parent için):
  step = (Kırmızı LOSE − Kırmızı giriş) / 4
  Çizgiler aşağıdan yukarı (Mavi Long için):
    MAVİ LOSE = Kırmızı giriş − 1×step     (yeni eklenen, Kırmızı giriş altında)
    ENTRY     = Kırmızı giriş çizgisi
    ST1       = Kırmızı giriş + 1×step
    ST2       = Kırmızı giriş + 2×step
    ST3       = Kırmızı giriş + 3×step
    (Kırmızı LOSE = Kırmızı giriş + 4×step, üst sınır)

  5 seviye/bölge: MAVI LOSE | ENTRY | ST1 | ST2 | ST3

  Mavi Short için tam simetri (Kırmızı Long parent → MAVİ LOSE üstte).

Flag (KONUM BAZLI — fiyat ENTRY bölgesindeyken açık):
  - Fiyat ENTRY bölgesinde (Kırmızı giriş ↔ ST1 arası) → flag açık
  - Diğer bölgelerde → flag kapalı
  - Tablo kurulurken bu bölgedeyse flag açılır

İşlem açılışı:
  - Flag açıkken fiyat ST1'i kâr yönüne cross → Mavi açılır
  - Tablo kurulurken fiyat ST1+ bölgesindeyse → otomatik açılış

Seviye geçişi:
  - ST1 → ST2 → ST3 (geri gidişlerde current_level güncellenir ama bildirim YOK)
  - Sadece highest_level ilerlediğinde Telegram bildirimi

Çıkış (2 yol):
  1. MAVİ LOSE ters cross (Long için aşağı, Short için yukarı) → Mavi tek başına kapanır
  2. Kırmızı LOSE kâr yönüne cross (Mavi WINRATE) → Kırmızı kapanır, zincirden Mavi de kapanır.
     Mavi de kendisi de close_red_and_dependents'ı tetikler (kapatmaya çalışır).
  3. Kırmızı herhangi bir sebepten kapanırsa → zincirden Mavi de kapanır.

Yeniden giriş:
  - Mavi kapanınca tablo silinmez (Kırmızı yaşadığı sürece).
  - flag_open, current_level sıfırlanır → yeniden açılış akışı tekrar başlar.
  - Sınırsız tekrar.

Flag bildirimleri Telegram'a atılmaz — sadece raporlarda görünür.
"""
import threading
import logging

from utils import crossed_up, crossed_down

log = logging.getLogger("BlueThread")


class BlueTable:
    __slots__ = ("red_trade_id", "red_side", "symbol", "side",
                 "mavi_lose", "entry_line", "levels", "red_lose",
                 "flag_open", "current_level", "active_trade")

    def __init__(self, red_trade, mavi_lose, entry_line, levels, red_lose):
        self.red_trade_id = red_trade.id
        self.red_side = red_trade.side
        self.symbol = red_trade.symbol
        # Mavi yön Kırmızı'nın tersi
        self.side = "LONG" if red_trade.side == "SHORT" else "SHORT"
        self.mavi_lose = mavi_lose          # Yeni eklenen çıkış çizgisi
        self.entry_line = entry_line        # = Kırmızı giriş çizgisi
        self.levels = dict(levels)          # ST1, ST2, ST3
        self.red_lose = red_lose            # Kırmızı LOSE = Mavi WINRATE
        self.flag_open = False
        self.current_level = None
        self.active_trade = None


class BlueThread(threading.Thread):

    LEVEL_ORDER = ["ST1", "ST2", "ST3"]

    def __init__(self, config, data_manager, trade_manager):
        super().__init__(name="BlueThread", daemon=True)
        self.cfg = config
        self.dm = data_manager
        self.tm = trade_manager
        self._stop = threading.Event()
        # red_trade_id -> BlueTable
        self.tables = {}
        self.tables_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    # OKUMA HELPERS (raporlar için)
    # ------------------------------------------------------------------
    def get_open_flags(self):
        """Açık Mavi flag'lerini döndür (raporlar/status için)."""
        result = []
        with self.tables_lock:
            for tbl in self.tables.values():
                if tbl.flag_open and tbl.active_trade is None:
                    result.append({"symbol": tbl.symbol, "thread": "BLUE",
                                   "side": tbl.side})
        return result

    # ------------------------------------------------------------------
    # TABLO OLUŞTURMA
    # ------------------------------------------------------------------
    def create_table_for_red(self, red_trade):
        """
        Kırmızı işlem açıldığında Mavi tablosunu kurar.
        step = (Kırmızı LOSE − Kırmızı giriş) / 4
        Mavi LOSE = entry − step, ST1..ST3 = entry + (1..3)×step
        """
        entry = red_trade.level_lines["ENTRY"]
        red_lose = red_trade.lose_line
        step = (red_lose - entry) / 4.0
        mavi_lose = entry - step
        levels = {
            "ST1": entry + step * 1,
            "ST2": entry + step * 2,
            "ST3": entry + step * 3,
        }

        table = BlueTable(red_trade, mavi_lose, entry, levels, red_lose)
        with self.tables_lock:
            self.tables[red_trade.id] = table

        # Telegram bildirim
        all_lines = {
            "MAVI LOSE": mavi_lose,
            "ENTRY (Kırmızı Giriş)": entry,
            **levels,
            "Kırmızı LOSE (Mavi WINRATE)": red_lose,
        }
        self.tm.tg.notify_thread_ready(red_trade, "BLUE", table.side, all_lines)

        # Tablo kurulurken fiyat durumunu kontrol et
        self._check_initial_position(table)

        return table

    def _check_initial_position(self, tbl):
        """Tablo kurulduğunda fiyat hangi bölgede?"""
        curr = self.dm.get_last_price(tbl.symbol)
        if curr is None:
            return

        zone = self._find_zone(tbl, curr)
        if zone is None:
            return

        if zone == "FLAG":
            tbl.flag_open = True
            self.tm.log_flag_event(tbl.symbol, "BLUE", tbl.side, "OPENED")
        elif zone in self.LEVEL_ORDER:
            # Otomatik açılış
            tbl.flag_open = True
            opened = self._open_blue(tbl, curr, initial_level=zone)
            if opened:
                self.tm.log_flag_event(tbl.symbol, "BLUE", tbl.side, "CONVERTED")

    def remove_table_for_red(self, red_trade_id):
        with self.tables_lock:
            tbl = self.tables.pop(red_trade_id, None)
        if not tbl:
            return
        if tbl.flag_open and tbl.active_trade is None:
            self.tm.log_flag_event(tbl.symbol, "BLUE", tbl.side, "DELETED")

    # ------------------------------------------------------------------
    # BÖLGE TESPİTİ
    # ------------------------------------------------------------------
    def _find_zone(self, tbl, price):
        """
        Fiyat hangi bölgede?
        Dönüş:
          "FLAG"     → ENTRY bölgesinde (Kırmızı giriş ↔ ST1 arası)
          "ST1/2/3"  → ilgili bölgede
          None       → tablo dışı veya MAVİ LOSE bölgesinde (Kırmızı giriş altı)
        """
        mavi_lose = tbl.mavi_lose
        entry = tbl.entry_line
        levels = tbl.levels
        red_lose = tbl.red_lose

        if tbl.side == "LONG":
            # Kırmızı Short, Mavi Long → tablo yukarı uzanır
            # MAVİ LOSE altta, Kırmızı LOSE üstte
            if price < mavi_lose or price > red_lose:
                return None
            if price < entry:
                # MAVİ LOSE bölgesi (Kırmızı giriş altı): flag/açılış olmaz
                return None
            if price < levels["ST1"]:
                return "FLAG"  # ENTRY bölgesi
            if price < levels["ST2"]:
                return "ST1"
            if price < levels["ST3"]:
                return "ST2"
            return "ST3"
        else:
            # Kırmızı Long, Mavi Short → tablo aşağı uzanır
            # MAVİ LOSE üstte, Kırmızı LOSE altta
            if price > mavi_lose or price < red_lose:
                return None
            if price > entry:
                return None
            if price > levels["ST1"]:
                return "FLAG"  # ENTRY bölgesi
            if price > levels["ST2"]:
                return "ST1"
            if price > levels["ST3"]:
                return "ST2"
            return "ST3"

    # ------------------------------------------------------------------
    # SCAN — her 1 sn'de çağrılır
    # ------------------------------------------------------------------
    def scan(self):
        # Kırmızı'sı bot hafızasında kapalı/yok olan tabloları temizle
        self._cleanup_dead_tables()

        with self.tables_lock:
            tbls = list(self.tables.values())

        for tbl in tbls:
            if self._stop.is_set():
                return
            try:
                self._tick_table(tbl)
            except Exception as e:
                log.exception(f"BlueThread tick hatası ({tbl.symbol}): {e}")

    def _cleanup_dead_tables(self):
        """
        Sadece bot hafızasındaki Kırmızı'ya bakar.
        """
        with self.tables_lock:
            ids_snapshot = list(self.tables.items())

        for red_id, tbl in ids_snapshot:
            red = self.tm.slots.get_red_for(tbl.symbol, tbl.red_side)
            red_missing = (red is None or red.id != red_id or red.closed)

            if red_missing:
                # Önce açık Mavi varsa kapat
                if tbl.active_trade and not tbl.active_trade.closed:
                    curr = self.dm.get_last_price(tbl.symbol)
                    try:
                        self.tm.close_trade(tbl.active_trade, "MAVİ KIRMIZI KAPANDI", curr)
                    except Exception as e:
                        log.error(f"Mavi acil kapatma hatası ({tbl.symbol}): {e}")
                # Flag açıksa raporda DELETED olarak işaretle
                if tbl.flag_open and tbl.active_trade is None:
                    self.tm.log_flag_event(tbl.symbol, "BLUE", tbl.side, "DELETED")
                with self.tables_lock:
                    self.tables.pop(red_id, None)

    # ------------------------------------------------------------------
    # TEK TABLO TICK
    # ------------------------------------------------------------------
    def _tick_table(self, tbl):
        prev, curr = self.dm.get_price_pair(tbl.symbol)
        if prev is None or curr is None:
            return

        # Aktif işlem kapanmışsa state'i temizle (yeniden giriş için hazırla)
        if tbl.active_trade and tbl.active_trade.closed:
            tbl.active_trade = None
            tbl.current_level = None
            tbl.flag_open = False

        # ----- A) AÇIK İŞLEM VARSA: çıkış kontrolü + seviye telemetri -----
        if tbl.active_trade and not tbl.active_trade.closed:
            self._handle_active_trade(tbl, prev, curr)
            return

        # ----- B) AÇIK İŞLEM YOK: konum bazlı flag + ST1 cross ile açılış -----
        zone = self._find_zone(tbl, curr)

        # Konum bazlı flag güncelleme
        if zone == "FLAG":
            if not tbl.flag_open:
                tbl.flag_open = True
                self.tm.log_flag_event(tbl.symbol, "BLUE", tbl.side, "OPENED")
        else:
            if tbl.flag_open:
                tbl.flag_open = False
                if zone in self.LEVEL_ORDER:
                    self.tm.log_flag_event(tbl.symbol, "BLUE", tbl.side, "CONVERTED")
                else:
                    self.tm.log_flag_event(tbl.symbol, "BLUE", tbl.side, "DELETED")

        # İşlem açılışı: ST1 giriş çizgisi cross
        if zone in self.LEVEL_ORDER:
            st1 = tbl.levels["ST1"]
            if tbl.side == "LONG":
                # Mavi Long: fiyat yukarı yönlü ST1 cross
                if crossed_up(prev, curr, st1):
                    self._open_blue(tbl, curr, initial_level=zone)
            else:
                # Mavi Short: fiyat aşağı yönlü ST1 cross
                if crossed_down(prev, curr, st1):
                    self._open_blue(tbl, curr, initial_level=zone)

    # ------------------------------------------------------------------
    # AÇIK İŞLEM YÖNETİMİ
    # ------------------------------------------------------------------
    def _handle_active_trade(self, tbl, prev, curr):
        trade = tbl.active_trade
        if trade is None or trade.closed:
            return

        # 1) MAVİ LOSE ters cross → Mavi tek başına kapanır
        if tbl.side == "LONG":
            # Mavi Long için ters yön = aşağı (MAVİ LOSE çizgisini aşağı kırdı)
            if crossed_down(prev, curr, tbl.mavi_lose):
                self.tm.close_trade(trade, "MAVİ LOSE EXIT", curr)
                tbl.active_trade = None
                tbl.current_level = None
                tbl.flag_open = False
                return
        else:
            # Mavi Short için ters yön = yukarı
            if crossed_up(prev, curr, tbl.mavi_lose):
                self.tm.close_trade(trade, "MAVİ LOSE EXIT", curr)
                tbl.active_trade = None
                tbl.current_level = None
                tbl.flag_open = False
                return

        # 2) Kırmızı LOSE'u kâr yönüne cross → Kırmızı'yı kapat (zincir Mavi'yi de kapatır)
        red_trade = self.tm.slots.get_red_for(tbl.symbol, tbl.red_side)
        if red_trade and not red_trade.closed:
            if tbl.side == "LONG":
                # Mavi Long kâr yönü = yukarı (Kırmızı Short için LOSE de yukarıda)
                if crossed_up(prev, curr, tbl.red_lose):
                    self.tm.close_red_and_dependents(
                        red_trade, "KIRMIZI LOSE (MAVİ WINRATE)", curr)
                    return
            else:
                # Mavi Short kâr yönü = aşağı
                if crossed_down(prev, curr, tbl.red_lose):
                    self.tm.close_red_and_dependents(
                        red_trade, "KIRMIZI LOSE (MAVİ WINRATE)", curr)
                    return

        # 3) Seviye telemetrisi: current_level iki yönlü güncellenir,
        # bildirim SADECE highest_level ilerlediğinde.
        new_zone = self._find_zone(tbl, curr)
        if new_zone and new_zone in self.LEVEL_ORDER and new_zone != tbl.current_level:
            tbl.current_level = new_zone
            trade.current_level = new_zone
            try:
                cur_idx = self.LEVEL_ORDER.index(new_zone)
                high_idx = (self.LEVEL_ORDER.index(trade.highest_level)
                            if trade.highest_level in self.LEVEL_ORDER
                            else -1)
                if cur_idx > high_idx:
                    trade.highest_level = new_zone
                    self.tm.tg.notify_level_change(trade, new_zone)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # AÇILIŞ
    # ------------------------------------------------------------------
    def _open_blue(self, tbl, entry_price, initial_level=None):
        red_trade = self.tm.slots.get_red_for(tbl.symbol, tbl.red_side)
        if not red_trade or red_trade.id != tbl.red_trade_id or red_trade.closed:
            return False

        level_lines = {
            "MAVI_LOSE": tbl.mavi_lose,
            "ENTRY": tbl.entry_line,
            **tbl.levels,
            "RED_LOSE": tbl.red_lose,
        }

        # Seviye fiyatın bölgesine göre
        if initial_level is None:
            zone = self._find_zone(tbl, entry_price)
            initial_level = zone if zone in self.LEVEL_ORDER else "ST1"

        trade = self.tm.open_trade(
            symbol=tbl.symbol, side=tbl.side, thread="BLUE",
            entry_price=entry_price,
            lose_line=tbl.mavi_lose,        # Mavi'nin kendi LOSE'u
            winrate_line=tbl.red_lose,      # Mavi WINRATE = Kırmızı LOSE
            level_lines=level_lines,
            current_level=initial_level,
            parent_red_trade=red_trade,
        )
        if not trade:
            return False

        tbl.active_trade = trade
        tbl.current_level = initial_level
        tbl.flag_open = False

        log.info(f"[{tbl.symbol}] MAVİ {tbl.side} açıldı @ {trade.entry_price} "
                 f"seviye={initial_level}")
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
