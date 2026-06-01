"""
🟣 MOR THREAD — YENİ TASARIM (Mavi ile birebir aynı, parent=Beyaz)

Yön:
  Beyaz Short → Mor Long
  Beyaz Long  → Mor Short

Tablo (Mor Long, Beyaz Short parent için):
  step = (Beyaz LOSE − Beyaz giriş) / 4
  Çizgiler aşağıdan yukarı:
    MOR LOSE  = Beyaz giriş − 1×step       (yeni eklenen, Beyaz giriş altında)
    ENTRY     = Beyaz giriş çizgisi
    ST1       = Beyaz giriş + 1×step
    ST2       = Beyaz giriş + 2×step
    ST3       = Beyaz giriş + 3×step
    (Beyaz LOSE = Beyaz giriş + 4×step, üst sınır)

  5 seviye/bölge: MOR LOSE | ENTRY | ST1 | ST2 | ST3

  Mor Short için tam simetri (Beyaz Long parent → MOR LOSE üstte).

Flag (KONUM BAZLI):
  - Fiyat ENTRY bölgesinde (Beyaz giriş ↔ ST1 arası) → flag açık

İşlem açılışı:
  - Flag açıkken fiyat ST1'i kâr yönüne cross → Mor açılır
  - Tablo kurulurken fiyat ST1+ → otomatik açılış

Seviye geçişi:
  - Sadece highest_level ilerlediğinde Telegram bildirimi (tek yönlü)

Çıkış (2 yol):
  1. MOR LOSE ters cross → Mor tek başına kapanır
  2. Beyaz LOSE kâr yönüne cross (Mor WINRATE) → Beyaz kapanır + zincir Mor.
  3. Beyaz herhangi bir sebepten kapanırsa → zincir Mor kapanır.

Yeniden giriş:
  - Mor kapanınca tablo silinmez (Beyaz yaşadığı sürece).
  - Sınırsız tekrar.
"""
import threading
import logging

from utils import crossed_up, crossed_down

log = logging.getLogger("PurpleThread")


class PurpleTable:
    __slots__ = ("white_trade_id", "white_side", "symbol", "side",
                 "mor_lose", "entry_line", "levels", "white_lose",
                 "flag_open", "current_level", "active_trade")

    def __init__(self, white_trade, mor_lose, entry_line, levels, white_lose):
        self.white_trade_id = white_trade.id
        self.white_side = white_trade.side
        self.symbol = white_trade.symbol
        # Mor yön Beyaz'ın tersi
        self.side = "LONG" if white_trade.side == "SHORT" else "SHORT"
        self.mor_lose = mor_lose            # Yeni eklenen çıkış çizgisi
        self.entry_line = entry_line        # = Beyaz giriş çizgisi
        self.levels = dict(levels)          # ST1, ST2, ST3
        self.white_lose = white_lose        # Beyaz LOSE = Mor WINRATE
        self.flag_open = False
        self.current_level = None
        self.active_trade = None


class PurpleThread(threading.Thread):

    LEVEL_ORDER = ["ST1", "ST2", "ST3"]

    def __init__(self, config, data_manager, trade_manager):
        super().__init__(name="PurpleThread", daemon=True)
        self.cfg = config
        self.dm = data_manager
        self.tm = trade_manager
        self._stop = threading.Event()
        # white_trade_id -> PurpleTable
        self.tables = {}
        self.tables_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    # OKUMA HELPERS (raporlar için)
    # ------------------------------------------------------------------
    def get_open_flags(self):
        """Açık Mor flag'lerini döndür (raporlar/status için)."""
        result = []
        with self.tables_lock:
            for tbl in self.tables.values():
                if tbl.flag_open and tbl.active_trade is None:
                    result.append({"symbol": tbl.symbol, "thread": "PURPLE",
                                   "side": tbl.side})
        return result

    # ------------------------------------------------------------------
    # TABLO OLUŞTURMA
    # ------------------------------------------------------------------
    def create_table_for_white(self, white_trade):
        """
        Beyaz işlem açıldığında Mor tablosunu kurar.
        step = (Beyaz LOSE − Beyaz giriş) / 4
        Mor LOSE = entry − step, ST1..ST3 = entry + (1..3)×step
        """
        entry = white_trade.level_lines["ENTRY"]
        white_lose = white_trade.lose_line
        step = (white_lose - entry) / 4.0
        mor_lose = entry - step
        levels = {
            "ST1": entry + step * 1,
            "ST2": entry + step * 2,
            "ST3": entry + step * 3,
        }

        table = PurpleTable(white_trade, mor_lose, entry, levels, white_lose)
        with self.tables_lock:
            self.tables[white_trade.id] = table

        # Telegram bildirim
        all_lines = {
            "MOR LOSE": mor_lose,
            "ENTRY (Beyaz Giriş)": entry,
            **levels,
            "Beyaz LOSE (Mor WINRATE)": white_lose,
        }
        self.tm.tg.notify_thread_ready(white_trade, "PURPLE", table.side, all_lines)

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
            self.tm.log_flag_event(tbl.symbol, "PURPLE", tbl.side, "OPENED")
        elif zone in self.LEVEL_ORDER:
            # Otomatik açılış
            tbl.flag_open = True
            opened = self._open_purple(tbl, curr, initial_level=zone)
            if opened:
                self.tm.log_flag_event(tbl.symbol, "PURPLE", tbl.side, "CONVERTED")

    def remove_table_for_white(self, white_trade_id):
        with self.tables_lock:
            tbl = self.tables.pop(white_trade_id, None)
        if not tbl:
            return
        if tbl.flag_open and tbl.active_trade is None:
            self.tm.log_flag_event(tbl.symbol, "PURPLE", tbl.side, "DELETED")

    # ------------------------------------------------------------------
    # BÖLGE TESPİTİ
    # ------------------------------------------------------------------
    def _find_zone(self, tbl, price):
        """
        Fiyat hangi bölgede?
        Dönüş:
          "FLAG"     → ENTRY bölgesinde (Beyaz giriş ↔ ST1 arası)
          "ST1/2/3"  → ilgili bölgede
          None       → tablo dışı veya MOR LOSE bölgesinde (Beyaz giriş altı)
        """
        mor_lose = tbl.mor_lose
        entry = tbl.entry_line
        levels = tbl.levels
        white_lose = tbl.white_lose

        if tbl.side == "LONG":
            # Beyaz Short, Mor Long → tablo yukarı uzanır
            # MOR LOSE altta, Beyaz LOSE üstte
            if price < mor_lose or price > white_lose:
                return None
            if price < entry:
                # MOR LOSE bölgesi (Beyaz giriş altı): flag/açılış olmaz
                return None
            if price < levels["ST1"]:
                return "FLAG"  # ENTRY bölgesi
            if price < levels["ST2"]:
                return "ST1"
            if price < levels["ST3"]:
                return "ST2"
            return "ST3"
        else:
            # Beyaz Long, Mor Short → tablo aşağı uzanır
            # MOR LOSE üstte, Beyaz LOSE altta
            if price > mor_lose or price < white_lose:
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
        # Beyaz'ı bot hafızasında kapalı/yok olan tabloları temizle
        self._cleanup_dead_tables()

        with self.tables_lock:
            tbls = list(self.tables.values())

        for tbl in tbls:
            if self._stop.is_set():
                return
            try:
                self._tick_table(tbl)
            except Exception as e:
                log.exception(f"PurpleThread tick hatası ({tbl.symbol}): {e}")

    def _cleanup_dead_tables(self):
        """
        Sadece bot hafızasındaki Beyaz'a bakar.
        """
        with self.tables_lock:
            ids_snapshot = list(self.tables.items())

        for white_id, tbl in ids_snapshot:
            white = self.tm.slots.get_white_for(tbl.symbol, tbl.white_side)
            white_missing = (white is None or white.id != white_id or white.closed)

            if white_missing:
                # Önce açık Mor varsa kapat
                if tbl.active_trade and not tbl.active_trade.closed:
                    curr = self.dm.get_last_price(tbl.symbol)
                    try:
                        self.tm.close_trade(tbl.active_trade, "MOR BEYAZ KAPANDI", curr)
                    except Exception as e:
                        log.error(f"Mor acil kapatma hatası ({tbl.symbol}): {e}")
                # Flag açıksa raporda DELETED olarak işaretle
                if tbl.flag_open and tbl.active_trade is None:
                    self.tm.log_flag_event(tbl.symbol, "PURPLE", tbl.side, "DELETED")
                with self.tables_lock:
                    self.tables.pop(white_id, None)

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
                self.tm.log_flag_event(tbl.symbol, "PURPLE", tbl.side, "OPENED")
        else:
            if tbl.flag_open:
                tbl.flag_open = False
                if zone in self.LEVEL_ORDER:
                    self.tm.log_flag_event(tbl.symbol, "PURPLE", tbl.side, "CONVERTED")
                else:
                    self.tm.log_flag_event(tbl.symbol, "PURPLE", tbl.side, "DELETED")

        # İşlem açılışı: ST1 giriş çizgisi cross
        if zone in self.LEVEL_ORDER:
            st1 = tbl.levels["ST1"]
            if tbl.side == "LONG":
                # Mor Long: fiyat yukarı yönlü ST1 cross
                if crossed_up(prev, curr, st1):
                    self._open_purple(tbl, curr, initial_level=zone)
            else:
                # Mor Short: fiyat aşağı yönlü ST1 cross
                if crossed_down(prev, curr, st1):
                    self._open_purple(tbl, curr, initial_level=zone)

    # ------------------------------------------------------------------
    # AÇIK İŞLEM YÖNETİMİ
    # ------------------------------------------------------------------
    def _handle_active_trade(self, tbl, prev, curr):
        trade = tbl.active_trade
        if trade is None or trade.closed:
            return

        # 1) MOR LOSE ters cross → Mor tek başına kapanır
        if tbl.side == "LONG":
            # Mor Long için ters yön = aşağı (MOR LOSE çizgisini aşağı kırdı)
            if crossed_down(prev, curr, tbl.mor_lose):
                self.tm.close_trade(trade, "MOR LOSE EXIT", curr)
                tbl.active_trade = None
                tbl.current_level = None
                tbl.flag_open = False
                return
        else:
            # Mor Short için ters yön = yukarı
            if crossed_up(prev, curr, tbl.mor_lose):
                self.tm.close_trade(trade, "MOR LOSE EXIT", curr)
                tbl.active_trade = None
                tbl.current_level = None
                tbl.flag_open = False
                return

        # 2) Beyaz LOSE'u kâr yönüne cross → Beyaz'ı kapat (zincir Mor'u da kapatır)
        white_trade = self.tm.slots.get_white_for(tbl.symbol, tbl.white_side)
        if white_trade and not white_trade.closed:
            if tbl.side == "LONG":
                # Mor Long kâr yönü = yukarı (Beyaz Short için LOSE de yukarıda)
                if crossed_up(prev, curr, tbl.white_lose):
                    self.tm.close_white_and_dependents(
                        white_trade, "BEYAZ LOSE (MOR WINRATE)", curr)
                    return
            else:
                # Mor Short kâr yönü = aşağı
                if crossed_down(prev, curr, tbl.white_lose):
                    self.tm.close_white_and_dependents(
                        white_trade, "BEYAZ LOSE (MOR WINRATE)", curr)
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
    def _open_purple(self, tbl, entry_price, initial_level=None):
        white_trade = self.tm.slots.get_white_for(tbl.symbol, tbl.white_side)
        if not white_trade or white_trade.id != tbl.white_trade_id or white_trade.closed:
            return False

        level_lines = {
            "MOR_LOSE": tbl.mor_lose,
            "ENTRY": tbl.entry_line,
            **tbl.levels,
            "WHITE_LOSE": tbl.white_lose,
        }

        # Seviye fiyatın bölgesine göre
        if initial_level is None:
            zone = self._find_zone(tbl, entry_price)
            initial_level = zone if zone in self.LEVEL_ORDER else "ST1"

        trade = self.tm.open_trade(
            symbol=tbl.symbol, side=tbl.side, thread="PURPLE",
            entry_price=entry_price,
            lose_line=tbl.mor_lose,         # Mor'un kendi LOSE'u
            winrate_line=tbl.white_lose,    # Mor WINRATE = Beyaz LOSE
            level_lines=level_lines,
            current_level=initial_level,
            parent_white_trade=white_trade,
        )
        if not trade:
            return False

        tbl.active_trade = trade
        tbl.current_level = initial_level
        tbl.flag_open = False

        log.info(f"[{tbl.symbol}] MOR {tbl.side} açıldı @ {trade.entry_price} "
                 f"seviye={initial_level}")
        return True

    # ------------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------------
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
