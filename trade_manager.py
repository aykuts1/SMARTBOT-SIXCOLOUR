"""
Trade Manager (YENİ EKOSİSTEM TASARIMI)

- Trade objesi (tek bir işlemi temsil eder; artık label/role/ekosistem alanları var)
- SlotManager: EKOSİSTEM bazlı (trade-id ile), çünkü aynı symbol+side'da
  birden fazla aynı renk işlem olabilir (2 Sarı, 3 Mavi).

  KIRMIZI ekosistemi (max 6 işlem):
      MAIN          = Kırmızı
      HEDGE_MAIN    = Mavi        (Kırmızı'nın hedge'i)
      TREND1        = Sarı 1
      HEDGE_TREND1  = Mavi 1
      TREND2        = Sarı 2
      HEDGE_TREND2  = Mavi 2

  BEYAZ ekosistemi (max 6 işlem): aynı yapı (Beyaz/Mor/Turuncu).

- TradeManager (açma, kapatma, stake, PnL, hard SL çakışma).
  * Zincir kapatma:
      close_ecosystem(top)        → ekosistemin TAMAMI kapanır
      close_primary_and_hedge(p)  → bir Sarı + kendi Mavi'si kapanır (top kalır)
      close_trade(t)              → tek işlem (hedge tek başına vs.)
"""
import math
import threading
import time
import logging

from utils import now_ts, fmt_money

log = logging.getLogger("TradeManager")


# Ekosistem slot isimleri
SLOT_MAIN = "MAIN"
SLOT_HEDGE_MAIN = "HEDGE_MAIN"
SLOT_TREND1 = "TREND1"
SLOT_HEDGE_TREND1 = "HEDGE_TREND1"
SLOT_TREND2 = "TREND2"
SLOT_HEDGE_TREND2 = "HEDGE_TREND2"

PRIMARY_SLOTS = (SLOT_MAIN, SLOT_TREND1, SLOT_TREND2)
ALL_SLOTS = (SLOT_MAIN, SLOT_HEDGE_MAIN,
             SLOT_TREND1, SLOT_HEDGE_TREND1,
             SLOT_TREND2, SLOT_HEDGE_TREND2)


def hedge_slot_for(primary_slot):
    return "HEDGE_" + primary_slot


# =========================================================================
# TRADE OBJESİ
# =========================================================================
class Trade:
    _id_counter = 0
    _id_lock = threading.Lock()

    def __init__(self, symbol, side, thread, entry_price, qty,
                 lose_line=None, winrate_line=None,
                 level_lines=None, current_level=None,
                 position_idx=None, hard_sl=None,
                 stake_usdt=0.0, label=None):
        with Trade._id_lock:
            Trade._id_counter += 1
            self.id = Trade._id_counter

        self.symbol = symbol
        self.side = side  # "LONG" / "SHORT"
        # Renk thread'i: "RED" / "BLUE" / "YELLOW" / "WHITE" / "PURPLE" / "ORANGE"
        # (raporlar bu renge göre toplar)
        self.thread = thread
        # Ekranda gösterilecek etiket: "KIRMIZI" / "SARI 1" / "MAVİ 2" ...
        self.label = label or thread
        self.entry_price = float(entry_price)
        self.qty = float(qty)
        self.opened_ts = now_ts()

        # Açılış anındaki stake (PnL hesabı için)
        self.stake_usdt = float(stake_usdt)

        self.current_level = current_level
        self.highest_level = current_level

        self.lose_line = lose_line
        self.winrate_line = winrate_line
        self.level_lines = dict(level_lines) if level_lines else {}

        self.position_idx = position_idx
        self.hard_sl = hard_sl

        # Kapanış bilgileri
        self.closed = False
        self.close_price = None
        self.close_ts = None
        self.exit_name = None
        self.pnl_usdt = 0.0
        self.pnl_pct = 0.0

        # Chandelier (Kırmızı/Sarı/Beyaz/Turuncu primary'leri)
        self.chandelier_distance = None
        self.chandelier_best_price = None
        self.chandelier_line = None

        # Geometri referansı
        self.lz = None
        self.winrate_zones = None

        # Ekosistem bağları: top_id, slot, parent_id
        self.extras = {}

    def duration_sec(self):
        end = self.close_ts if self.closed else now_ts()
        return max(0, end - self.opened_ts)


# =========================================================================
# EKOSİSTEM
# =========================================================================
class Ecosystem:
    __slots__ = ("top_id", "symbol", "kind", "members")

    def __init__(self, top_trade):
        self.top_id = top_trade.id
        self.symbol = top_trade.symbol
        self.kind = top_trade.thread  # "RED" veya "WHITE"
        self.members = {s: None for s in ALL_SLOTS}
        self.members[SLOT_MAIN] = top_trade


# =========================================================================
# SLOT MANAGER (ekosistem bazlı)
# =========================================================================
class SlotManager:

    def __init__(self):
        self.lock = threading.RLock()
        self.by_id = {}        # trade.id -> Trade (yalnız açık olanlar tutulur)
        self.ecosystems = {}   # top_trade_id -> Ecosystem

    # ------------------------------------------------------------------
    # AÇILABİLİR Mİ?
    # ------------------------------------------------------------------
    def _has_open_top(self, symbol, kind):
        for eco in self.ecosystems.values():
            if eco.symbol == symbol and eco.kind == kind:
                main = eco.members.get(SLOT_MAIN)
                if main and not main.closed:
                    return True
        return False

    def red_can_open(self, symbol):
        with self.lock:
            if self._has_open_top(symbol, "RED"):
                return (False, "COİNDE KIRMIZI VAR")
            return (True, None)

    def white_can_open(self, symbol):
        with self.lock:
            if self._has_open_top(symbol, "WHITE"):
                return (False, "COİNDE BEYAZ VAR")
            return (True, None)

    def slot_can_open(self, top_id, slot):
        with self.lock:
            eco = self.ecosystems.get(top_id)
            if not eco:
                return (False, "EKOSİSTEM YOK")
            m = eco.members.get(slot)
            if m is not None and not m.closed:
                return (False, "SLOT DOLU: %s" % slot)
            return (True, None)

    # ------------------------------------------------------------------
    # KAYIT
    # ------------------------------------------------------------------
    def register_top(self, trade):
        with self.lock:
            self.by_id[trade.id] = trade
            self.ecosystems[trade.id] = Ecosystem(trade)
            trade.extras["top_id"] = trade.id
            trade.extras["slot"] = SLOT_MAIN

    def register_member(self, trade, top_id, slot, parent_id=None):
        with self.lock:
            self.by_id[trade.id] = trade
            eco = self.ecosystems.get(top_id)
            if eco is not None:
                eco.members[slot] = trade
            trade.extras["top_id"] = top_id
            trade.extras["slot"] = slot
            if parent_id is not None:
                trade.extras["parent_id"] = parent_id

    # ------------------------------------------------------------------
    # OKUMA
    # ------------------------------------------------------------------
    def get_ecosystem(self, top_id):
        with self.lock:
            return self.ecosystems.get(top_id)

    def get_trade_by_id(self, tid):
        with self.lock:
            t = self.by_id.get(tid)
            if t is not None and not t.closed:
                return t
            return None

    def get_top_for_symbol(self, symbol, kind):
        with self.lock:
            for eco in self.ecosystems.values():
                if eco.symbol == symbol and eco.kind == kind:
                    main = eco.members.get(SLOT_MAIN)
                    if main and not main.closed:
                        return main
            return None

    def get_member(self, top_id, slot):
        with self.lock:
            eco = self.ecosystems.get(top_id)
            if not eco:
                return None
            m = eco.members.get(slot)
            if m is not None and not m.closed:
                return m
            return None

    # ------------------------------------------------------------------
    # SİLME
    # ------------------------------------------------------------------
    def unregister(self, trade):
        with self.lock:
            self.by_id.pop(trade.id, None)

            slot = trade.extras.get("slot")
            top_id = trade.extras.get("top_id")

            if slot == SLOT_MAIN:
                # Top kapandı → ekosistemi kaldır
                self.ecosystems.pop(trade.id, None)
            else:
                eco = self.ecosystems.get(top_id)
                if eco is not None and slot:
                    if eco.members.get(slot) is trade:
                        eco.members[slot] = None

    # ------------------------------------------------------------------
    # TOPLU OKUMA (raporlar / hard SL)
    # ------------------------------------------------------------------
    def get_all_open(self):
        with self.lock:
            return [t for t in self.by_id.values() if not t.closed]

    def get_open_by_thread(self, thread):
        with self.lock:
            return [t for t in self.by_id.values()
                    if t.thread == thread and not t.closed]

    def get_open_by_symbol_side(self, symbol, side):
        with self.lock:
            return [t for t in self.by_id.values()
                    if t.symbol == symbol and t.side == side and not t.closed]

    def count_by_thread(self):
        counts = {"RED": 0, "BLUE": 0, "YELLOW": 0,
                  "WHITE": 0, "PURPLE": 0, "ORANGE": 0}
        with self.lock:
            for t in self.by_id.values():
                if not t.closed:
                    counts[t.thread] = counts.get(t.thread, 0) + 1
        return counts


# =========================================================================
# TRADE MANAGER
# =========================================================================
class TradeManager:
    def __init__(self, config, data_manager, telegram_notifier):
        self.cfg = config
        self.dm = data_manager
        self.tg = telegram_notifier

        self.slots = SlotManager()

        # History
        self.closed_trades_history = []
        self.flag_history = []
        self.errors_history = []

        # Sayaçlar
        self._insufficient_balance_count = 0
        self._slot_full_count = 0
        self._error_count = 0

        # Rate limit koruması
        self._order_lock = threading.Lock()
        self._last_order_ts = 0.0
        self._order_min_gap_sec = 0.1

    # ------------------------------------------------------------------
    # STAKE
    # ------------------------------------------------------------------
    def get_stake(self):
        bal = self.dm.update_balance()
        return bal * (self.cfg.stake_pct / 100.0)

    # ------------------------------------------------------------------
    # YARDIMCILAR
    # ------------------------------------------------------------------
    def _position_idx(self, side):
        return 1 if side == "LONG" else 2

    def _order_side(self, side):
        return "Buy" if side == "LONG" else "Sell"

    def _close_side(self, side):
        return "Sell" if side == "LONG" else "Buy"

    def _calc_qty(self, symbol, entry_price, stake):
        info = self.dm.get_instrument_info(symbol)
        if not info:
            return 0.0
        if entry_price <= 0:
            return 0.0
        raw = (stake * self.cfg.leverage) / entry_price
        step = info["qtyStep"]
        if step <= 0:
            return 0.0
        qty = math.floor(raw / step) * step
        if step < 1:
            decimals = max(0, -int(math.floor(math.log10(step))))
        else:
            decimals = 0
        qty = round(qty, decimals + 4)
        if qty < info["minOrderQty"]:
            return 0.0
        return qty

    def _round_to_tick(self, price, tick_size, side, is_sl=True):
        if tick_size <= 0:
            return price
        if is_sl:
            if side == "LONG":
                return math.floor(price / tick_size) * tick_size
            else:
                return math.ceil(price / tick_size) * tick_size
        return round(price / tick_size) * tick_size

    def _calc_hard_sl(self, symbol, side, entry_price):
        pct = self.cfg.hard_sl_pct / 100.0
        if side == "LONG":
            raw = entry_price * (1.0 - pct)
        else:
            raw = entry_price * (1.0 + pct)

        info = self.dm.get_instrument_info(symbol)
        tick = info["tickSize"] if info else 0.0
        if tick > 0:
            return self._round_to_tick(raw, tick, side, is_sl=True)
        return round(raw, 8)

    def _decide_effective_hard_sl(self, symbol, side, new_hard_sl):
        existing = self.slots.get_open_by_symbol_side(symbol, side)
        existing_sls = [t.hard_sl for t in existing if t.hard_sl is not None]
        if not existing_sls:
            return new_hard_sl
        if side == "SHORT":
            return max(new_hard_sl, max(existing_sls))
        else:
            return min(new_hard_sl, min(existing_sls))

    def _rate_limit_order(self):
        with self._order_lock:
            gap = time.time() - self._last_order_ts
            if gap < self._order_min_gap_sec:
                time.sleep(self._order_min_gap_sec - gap)
            self._last_order_ts = time.time()

    # ------------------------------------------------------------------
    # AÇMA
    # ------------------------------------------------------------------
    def open_trade(self, symbol, side, thread, entry_price,
                   label=None, role="TOP", top_id=None, slot=None,
                   parent_id=None,
                   lose_line=None, winrate_line=None,
                   level_lines=None, current_level=None):
        """
        Yeni işlem aç.
        role="TOP"    → Kırmızı/Beyaz (ekosistem kurar)
        role="MEMBER" → Mavi/Sarı/... (top_id + slot gerekli)
        """
        # Slot kontrolü
        if role == "TOP":
            if thread == "RED":
                ok, msg = self.slots.red_can_open(symbol)
            else:
                ok, msg = self.slots.white_can_open(symbol)
            if not ok:
                self._slot_full_count += 1
                self.tg.notify_slot_full(symbol, side, thread, msg)
                return None
        else:
            ok, msg = self.slots.slot_can_open(top_id, slot)
            if not ok:
                # Üye slot dolu/eksik → sessiz geç (thread tekrar dener)
                return None

        # Stake
        stake = self.get_stake()
        if stake <= 0:
            self._insufficient_balance_count += 1
            self.tg.notify_insufficient_balance(symbol, side, thread, entry_price)
            return None

        qty = self._calc_qty(symbol, entry_price, stake)
        if qty <= 0:
            self._insufficient_balance_count += 1
            self.tg.notify_insufficient_balance(symbol, side, thread, entry_price)
            return None

        # Hard SL
        own_hard_sl = self._calc_hard_sl(symbol, side, entry_price)
        effective_sl = self._decide_effective_hard_sl(symbol, side, own_hard_sl)

        # Bybit order
        try:
            self._rate_limit_order()
            self.dm.place_market_order(
                symbol=symbol,
                side=self._order_side(side),
                qty=qty,
                position_idx=self._position_idx(side),
                stop_loss=effective_sl,
            )
        except Exception as e:
            self._error_count += 1
            self.errors_history.append({
                "ts": now_ts(), "title": "Order açılamadı",
                "symbol": symbol, "module": "TradeManager", "detail": str(e),
            })
            self.tg.notify_error("Order açılamadı", symbol, "TradeManager", str(e))
            return None

        # Pozisyon doğrulama
        time.sleep(1.5)
        verified = self._verify_position_open(symbol, self._position_idx(side))
        if not verified:
            self._error_count += 1
            self.tg.notify_error(
                "Pozisyon doğrulanamadı (Bybit'te açılmamış olabilir)",
                symbol, "TradeManager",
                f"side={side} qty={qty} entry={entry_price}",
            )
            return None

        real_entry = float(entry_price)

        trade = Trade(
            symbol=symbol, side=side, thread=thread,
            entry_price=real_entry, qty=qty,
            lose_line=lose_line, winrate_line=winrate_line,
            level_lines=level_lines, current_level=current_level,
            position_idx=self._position_idx(side),
            hard_sl=own_hard_sl,
            stake_usdt=stake,
            label=label,
        )

        # Register
        if role == "TOP":
            self.slots.register_top(trade)
        else:
            self.slots.register_member(trade, top_id, slot, parent_id=parent_id)

        self.tg.notify_trade_open(trade, hard_sl=effective_sl)
        log.info(f"İşlem açıldı: {trade.label} {side} {symbol} @ {real_entry} qty={qty} "
                 f"stake={fmt_money(stake)} own_sl={own_hard_sl} effective_sl={effective_sl}")
        return trade

    def _verify_position_open(self, symbol, position_idx):
        try:
            positions = self.dm.get_open_positions(symbol)
            for p in positions:
                pidx = int(p.get("positionIdx", 0))
                size = float(p.get("size", 0))
                if pidx == position_idx and size > 0:
                    return True
            return False
        except Exception as e:
            log.error(f"Pozisyon doğrulama hatası {symbol}: {e}")
            return False

    # ------------------------------------------------------------------
    # KAPATMA (tek işlem)
    # ------------------------------------------------------------------
    def close_trade(self, trade, exit_name, close_price_hint=None):
        if trade.closed:
            return False

        try:
            self._rate_limit_order()
            self.dm.close_position_market(
                symbol=trade.symbol,
                side_to_close=self._close_side(trade.side),
                qty=trade.qty,
                position_idx=trade.position_idx,
            )
        except Exception as e:
            self._error_count += 1
            self.errors_history.append({
                "ts": now_ts(), "title": "Order kapatılamadı",
                "symbol": trade.symbol, "module": "TradeManager", "detail": str(e),
            })
            self.tg.notify_error("Order kapatılamadı", trade.symbol, "TradeManager", str(e))
            close_price = close_price_hint if close_price_hint else trade.entry_price
            self._finalize_close(trade, exit_name, close_price)
            return False

        time.sleep(0.5)
        if close_price_hint is not None:
            actual_close = float(close_price_hint)
        else:
            lp = self.dm.get_last_price(trade.symbol)
            actual_close = float(lp) if lp is not None else trade.entry_price

        self._finalize_close(trade, exit_name, actual_close)
        return True

    def _finalize_close(self, trade, exit_name, close_price):
        trade.closed = True
        trade.close_price = float(close_price)
        trade.close_ts = now_ts()
        trade.exit_name = exit_name

        if trade.entry_price == 0:
            pnl_raw = 0.0
        elif trade.side == "LONG":
            pnl_raw = (trade.close_price - trade.entry_price) / trade.entry_price
        else:
            pnl_raw = (trade.entry_price - trade.close_price) / trade.entry_price

        stake = trade.stake_usdt
        trade.pnl_usdt = stake * self.cfg.leverage * pnl_raw
        trade.pnl_pct = pnl_raw * self.cfg.leverage * 100.0

        self.closed_trades_history.append(trade)
        self.slots.unregister(trade)

        self.tg.notify_trade_close(trade)
        log.info(f"İşlem kapandı: {trade.label} {trade.side} {trade.symbol} "
                 f"@ {trade.close_price} PnL={fmt_money(trade.pnl_usdt)} "
                 f"({trade.pnl_pct:+.2f}%) — {exit_name}")

    # ------------------------------------------------------------------
    # ZİNCİR KAPATMA
    # ------------------------------------------------------------------
    def close_ecosystem(self, top_trade, exit_name, close_price_hint=None):
        """Ekosistemin TAMAMINI kapatır (önce hedge'ler + trend'ler, sonra top)."""
        if top_trade.thread not in ("RED", "WHITE"):
            log.warning(f"close_ecosystem top olmayan trade için çağrıldı: {top_trade.thread}")
            self.close_trade(top_trade, exit_name, close_price_hint)
            return

        dep_reason = "KIRMIZI KAPANDI" if top_trade.thread == "RED" else "BEYAZ KAPANDI"
        eco = self.slots.get_ecosystem(top_trade.id)
        if eco is not None:
            for slot in (SLOT_HEDGE_MAIN, SLOT_HEDGE_TREND1, SLOT_HEDGE_TREND2,
                         SLOT_TREND1, SLOT_TREND2):
                m = eco.members.get(slot)
                if m is not None and not m.closed:
                    self.close_trade(m, dep_reason, close_price_hint)

        self.close_trade(top_trade, exit_name, close_price_hint)

    def close_primary_and_hedge(self, primary, exit_name, close_price_hint=None):
        """Bir Sarı/Turuncu primary'sini + kendi hedge'ini kapatır (top kalır)."""
        slot = primary.extras.get("slot")
        top_id = primary.extras.get("top_id")

        if slot == SLOT_MAIN:
            # Aslında top → tüm ekosistem
            self.close_ecosystem(primary, exit_name, close_price_hint)
            return

        dep_reason = "SARI KAPANDI" if primary.thread == "YELLOW" else "TURUNCU KAPANDI"
        eco = self.slots.get_ecosystem(top_id)
        if eco is not None and slot:
            hedge = eco.members.get(hedge_slot_for(slot))
            if hedge is not None and not hedge.closed:
                self.close_trade(hedge, dep_reason, close_price_hint)

        self.close_trade(primary, exit_name, close_price_hint)

    # ------------------------------------------------------------------
    # FLAG HISTORY
    # ------------------------------------------------------------------
    def log_flag_event(self, symbol, thread, side, event):
        self.flag_history.append({
            "ts": now_ts(),
            "symbol": symbol,
            "thread": thread,
            "side": side,
            "event": event,
        })

    # ------------------------------------------------------------------
    # HISTORY OKUMA
    # ------------------------------------------------------------------
    def get_closed_trades_window(self, start_ts, end_ts):
        return [t for t in self.closed_trades_history
                if t.close_ts is not None and start_ts <= t.close_ts <= end_ts]

    def get_flag_events_window(self, start_ts, end_ts):
        return [e for e in self.flag_history if start_ts <= e["ts"] <= end_ts]

    def get_errors_window(self, start_ts, end_ts):
        return [e for e in self.errors_history if start_ts <= e["ts"] <= end_ts]

    def get_all_closed_trades(self):
        return list(self.closed_trades_history)

    def get_counters(self):
        return {
            "insufficient_balance": self._insufficient_balance_count,
            "slot_full": self._slot_full_count,
            "error": self._error_count,
        }
