"""
Pozisyon yöneticisi: Açık pozisyonların state'i, stop taşıma, CE güncelleme,
çıkış kararı.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import config
from bybit_client import BybitClient
from telegram_bot import TelegramNotifier
import telegram_bot as tg_fmt

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    """Açık pozisyon state'i."""
    symbol: str
    side: str                  # "long" / "short"
    entry_price: float
    qty: float
    initial_stop: float        # %1 stop seviyesi (giriş anında)
    current_stop: float        # şu anki borsa SL seviyesi
    entry_atr: float           # giriş anındaki ATR (CE ve BE hesabı için)
    ce_multiplier: float       # şu anki CE çarpanı (1.0 → 0.5'e geçer)
    ce_level: float            # şu anki CE seviyesi
    running_high: float        # pozisyon açıldığından beri görülen en yüksek fiyat
    running_low: float         # pozisyon açıldığından beri görülen en düşük fiyat
    stake: float               # bu pozisyon için kullanılan stake
    opened_at: float           # unix timestamp
    be_moved: bool = False     # stop BE'ye taşındı mı
    ce_tightened: bool = False # CE 0.5'e sıkılaştı mı


class PositionManager:
    """Açık pozisyonları yönetir."""

    def __init__(
        self,
        client: BybitClient,
        notifier: TelegramNotifier,
        stake_per_trade: float,
    ) -> None:
        self.client = client
        self.notifier = notifier
        self.stake = stake_per_trade
        self._positions: Dict[str, OpenPosition] = {}
        self._lock = threading.RLock()

    # ============= STATE ERIŞIMI =============
    def count(self) -> int:
        with self._lock:
            return len(self._positions)

    def has(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._positions

    def get_all(self) -> List[OpenPosition]:
        with self._lock:
            return list(self._positions.values())

    def get(self, symbol: str) -> Optional[OpenPosition]:
        with self._lock:
            return self._positions.get(symbol)

    def _remove(self, symbol: str) -> None:
        with self._lock:
            self._positions.pop(symbol, None)

    def _add(self, pos: OpenPosition) -> None:
        with self._lock:
            self._positions[pos.symbol] = pos

    # ============= POZİSYON AÇMA =============
    def open_position(
        self,
        symbol: str,
        side: str,
        entry_atr: float,
    ) -> Optional[OpenPosition]:
        """
        Limit emir (market gibi) ile pozisyon aç, %1 stop borsa seviyesinde set et,
        CE giriş fiyatının 1 ATR gerisinde başlat, state'i kaydet.

        Hata olursa None döner.
        """
        if entry_atr <= 0:
            logger.warning(f"{symbol}: entry_atr 0, pozisyon açılmayacak")
            return None

        # 1) Anlık fiyat
        try:
            last_price = self.client.fetch_last_price(symbol)
        except Exception as e:
            logger.error(f"{symbol}: fiyat alınamadı - {e}")
            self.notifier.send(tg_fmt.fmt_error(f"{symbol} fiyat", str(e)))
            return None

        # 2) Limit fiyatı (market gibi)
        if side == "long":
            limit_price = last_price * (1 + config.LIMIT_SLIPPAGE)
            limit_price = self.client.round_price(symbol, limit_price, round_up=True)
        else:
            limit_price = last_price * (1 - config.LIMIT_SLIPPAGE)
            limit_price = self.client.round_price(symbol, limit_price, round_up=False)

        # 3) Miktar
        notional = self.stake * config.LEVERAGE
        raw_qty = notional / limit_price
        qty = self.client.round_qty(symbol, raw_qty)

        info = self.client.fetch_instrument_info(symbol)
        if qty < info["min_qty"]:
            logger.warning(
                f"{symbol}: qty {qty} < min_qty {info['min_qty']}, atlanıyor"
            )
            return None
        if qty <= 0:
            logger.warning(f"{symbol}: qty 0, atlanıyor")
            return None

        # 4) Emir gönder
        bybit_side = "Buy" if side == "long" else "Sell"
        try:
            self.client.place_limit_order(
                symbol=symbol,
                side=bybit_side,
                qty=qty,
                price=limit_price,
                reduce_only=False,
            )
        except Exception as e:
            logger.error(f"{symbol}: emir hatası - {e}")
            self.notifier.send(tg_fmt.fmt_error(f"{symbol} entry order", str(e)))
            return None

        # 5) Emrin dolmasını bekle
        filled_position = self._wait_for_fill(symbol, max_wait=15)
        if filled_position is None:
            logger.warning(f"{symbol}: emir dolmadı, iptal ediliyor")
            self.client.cancel_all_orders(symbol)
            return None

        # Gerçek giriş fiyatı pozisyondan alınır
        try:
            real_entry = float(filled_position.get("avgPrice", limit_price))
        except (TypeError, ValueError):
            real_entry = limit_price
        try:
            real_qty = float(filled_position.get("size", qty))
        except (TypeError, ValueError):
            real_qty = qty

        # 6) %1 stop hesapla ve borsa seviyesinde set et
        if side == "long":
            stop_price = real_entry * (1 - config.INITIAL_STOP_PERCENT)
            stop_price = self.client.round_price(symbol, stop_price, round_up=False)
        else:
            stop_price = real_entry * (1 + config.INITIAL_STOP_PERCENT)
            stop_price = self.client.round_price(symbol, stop_price, round_up=True)

        # 7) CE başlangıç seviyesi: entry'nin 1 ATR gerisinde
        if side == "long":
            initial_ce = real_entry - config.CE_INITIAL_MULTIPLIER * entry_atr
        else:
            initial_ce = real_entry + config.CE_INITIAL_MULTIPLIER * entry_atr

        try:
            self.client.set_trading_stop(
                symbol=symbol,
                stop_loss=stop_price,
            )
        except Exception as e:
            logger.error(f"{symbol}: stop set hatası - {e}")
            self.notifier.send(tg_fmt.fmt_error(f"{symbol} stop set", str(e)))
            # Acil durum: pozisyonu kapat
            try:
                close_side = "Sell" if side == "long" else "Buy"
                self.client.place_market_order(
                    symbol, close_side, real_qty, reduce_only=True
                )
            except Exception as e2:
                logger.error(f"{symbol}: acil kapatma da başarısız - {e2}")
            return None

        # 8) State kaydet
        pos = OpenPosition(
            symbol=symbol,
            side=side,
            entry_price=real_entry,
            qty=real_qty,
            initial_stop=stop_price,
            current_stop=stop_price,
            entry_atr=entry_atr,
            ce_multiplier=config.CE_INITIAL_MULTIPLIER,
            ce_level=initial_ce,
            running_high=real_entry,   # başlangıçta entry
            running_low=real_entry,    # başlangıçta entry
            stake=self.stake,
            opened_at=time.time(),
        )
        self._add(pos)
        return pos

    def _wait_for_fill(
        self, symbol: str, max_wait: int = 15
    ) -> Optional[Dict]:
        """Limit emrin dolmasını bekle, dolarsa pozisyon dict'i döndür."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                pos = self.client.fetch_position(symbol)
                if pos:
                    return pos
            except Exception as e:
                logger.warning(f"{symbol}: fill bekleme sırasında hata - {e}")
            time.sleep(1)
        return None

    # ============= ÇIKIŞ TARAMASI =============
    def scan_exits(self) -> List[Tuple[OpenPosition, str, float, float, float]]:
        """
        Her açık pozisyon için:
          - Borsa'da hâlâ açık mı? Kapanmışsa SL tetiklenmiş → kapanış işle
          - Açıksa BE taşıma, CE sıkılaştırma, CE trailing, CE tetik kontrolü

        Returns: kapanan pozisyonlar listesi
          [(pos, reason, exit_price, pnl_usdt, pnl_pct), ...]
        """
        closed = []

        for pos in self.get_all():
            try:
                bybit_pos = self.client.fetch_position(pos.symbol)
            except Exception as e:
                logger.warning(f"{pos.symbol}: position fetch hata - {e}")
                continue

            # ===== POZİSYON BORSA'DA YOK = SL TETİKLENDİ =====
            if bybit_pos is None:
                exit_price, pnl_usdt = self._handle_external_close(pos)
                pnl_pct = self._compute_pnl_pct(pos, exit_price)
                self._remove(pos.symbol)
                self.client.cancel_all_orders(pos.symbol)
                closed.append((pos, "Stop Loss tetiklendi", exit_price, pnl_usdt, pnl_pct))
                continue

            # ===== POZİSYON HÂLÂ AÇIK =====
            try:
                last_price = self.client.fetch_last_price(pos.symbol)
            except Exception as e:
                logger.warning(f"{pos.symbol}: fiyat alınamadı - {e}")
                continue

            # Running high/low güncelle
            if last_price > pos.running_high:
                pos.running_high = last_price
            if last_price < pos.running_low:
                pos.running_low = last_price

            # BE taşıma kontrolü
            self._maybe_move_to_be(pos, last_price)

            # CE sıkılaştırma kontrolü
            self._maybe_tighten_ce(pos, last_price)

            # CE seviyesini güncelle
            self._update_ce_level(pos)

            # CE tetiklendi mi?
            ce_hit = self._check_ce_hit(pos, last_price)
            if ce_hit:
                exit_price, pnl_usdt = self._close_by_ce(pos)
                if exit_price > 0:
                    pnl_pct = self._compute_pnl_pct(pos, exit_price)
                    self._remove(pos.symbol)
                    closed.append((pos, "Chandelier Exit tetiklendi", exit_price, pnl_usdt, pnl_pct))

        return closed

    # ============= BE TAŞIMA =============
    def _maybe_move_to_be(self, pos: OpenPosition, last_price: float) -> None:
        """0.5 ATR kârda stop'u entry + 0.2 ATR seviyesine taşı."""
        if pos.be_moved:
            return

        trigger_distance = config.BE_TRIGGER_ATR * pos.entry_atr

        if pos.side == "long":
            profit = last_price - pos.entry_price
            if profit < trigger_distance:
                return
            new_stop = pos.entry_price + config.BE_OFFSET_ATR * pos.entry_atr
            new_stop = self.client.round_price(pos.symbol, new_stop, round_up=False)
            if new_stop <= pos.current_stop:
                pos.be_moved = True
                return
        else:
            profit = pos.entry_price - last_price
            if profit < trigger_distance:
                return
            new_stop = pos.entry_price - config.BE_OFFSET_ATR * pos.entry_atr
            new_stop = self.client.round_price(pos.symbol, new_stop, round_up=True)
            if new_stop >= pos.current_stop:
                pos.be_moved = True
                return

        try:
            self.client.set_trading_stop(pos.symbol, stop_loss=new_stop)
            pos.current_stop = new_stop
            pos.be_moved = True
            logger.info(f"{pos.symbol}: stop BE'ye taşındı → {new_stop}")
        except Exception as e:
            logger.warning(f"{pos.symbol}: BE taşıma hata - {e}")

    # ============= CE SIKILAŞTIRMA =============
    def _maybe_tighten_ce(self, pos: OpenPosition, last_price: float) -> None:
        """1 ATR kârda CE çarpanını 0.5'e indir."""
        if pos.ce_tightened:
            return

        trigger_distance = config.CE_TIGHTEN_TRIGGER_ATR * pos.entry_atr

        if pos.side == "long":
            profit = last_price - pos.entry_price
        else:
            profit = pos.entry_price - last_price

        if profit >= trigger_distance:
            pos.ce_multiplier = config.CE_TIGHT_MULTIPLIER
            pos.ce_tightened = True
            logger.info(f"{pos.symbol}: CE sıkılaştı → {config.CE_TIGHT_MULTIPLIER} ATR")

    # ============= CE SEVİYE GÜNCELLEMESİ (TRAILING) =============
    def _update_ce_level(self, pos: OpenPosition) -> None:
        """
        Trailing CE:
          Long: yeni CE = running_high - multiplier × entry_atr (sadece yukarı)
          Short: yeni CE = running_low + multiplier × entry_atr (sadece aşağı)
        """
        if pos.side == "long":
            new_ce = pos.running_high - pos.ce_multiplier * pos.entry_atr
            if new_ce > pos.ce_level:
                pos.ce_level = new_ce
        else:
            new_ce = pos.running_low + pos.ce_multiplier * pos.entry_atr
            if new_ce < pos.ce_level:
                pos.ce_level = new_ce

    # ============= CE TETİK KONTROLÜ =============
    def _check_ce_hit(self, pos: OpenPosition, last_price: float) -> bool:
        if pos.side == "long":
            return last_price < pos.ce_level
        return last_price > pos.ce_level

    # ============= CE İLE KAPATMA =============
    def _close_by_ce(self, pos: OpenPosition) -> Tuple[float, float]:
        """CE tetiklendi → market-gibi-limit ile kapat."""
        try:
            last_price = self.client.fetch_last_price(pos.symbol)
        except Exception as e:
            logger.error(f"{pos.symbol}: kapanış fiyat alınamadı - {e}")
            return 0.0, 0.0

        # Bekleyen emirleri iptal et (pozisyon SL'i otomatik silinir kapanışta)
        try:
            self.client.cancel_all_orders(pos.symbol)
        except Exception:
            pass

        # Limit (market gibi) ile kapat
        if pos.side == "long":
            limit_price = last_price * (1 - config.LIMIT_SLIPPAGE)
            limit_price = self.client.round_price(pos.symbol, limit_price, round_up=False)
            close_side = "Sell"
        else:
            limit_price = last_price * (1 + config.LIMIT_SLIPPAGE)
            limit_price = self.client.round_price(pos.symbol, limit_price, round_up=True)
            close_side = "Buy"

        try:
            self.client.place_limit_order(
                symbol=pos.symbol,
                side=close_side,
                qty=pos.qty,
                price=limit_price,
                reduce_only=True,
            )
        except Exception as e:
            logger.error(f"{pos.symbol}: CE kapanış limit emir hata - {e}")
            try:
                self.client.place_market_order(
                    pos.symbol, close_side, pos.qty, reduce_only=True
                )
            except Exception as e2:
                logger.error(f"{pos.symbol}: CE kapanış market da başarısız - {e2}")
                self.notifier.send(tg_fmt.fmt_error(f"{pos.symbol} CE close", str(e2)))
                return 0.0, 0.0

        # Emrin gerçekleşmesini bekle
        time.sleep(2)

        # PnL'i al
        exit_price, pnl = self._fetch_closed_pnl(pos)
        if exit_price <= 0:
            exit_price = last_price
            if pos.side == "long":
                pnl = (exit_price - pos.entry_price) * pos.qty
            else:
                pnl = (pos.entry_price - exit_price) * pos.qty
        return exit_price, pnl

    # ============= DIŞ KAPANIŞ (SL TETİKLENDİ) =============
    def _handle_external_close(self, pos: OpenPosition) -> Tuple[float, float]:
        """Bybit'te pozisyon zaten kapanmış → closed PnL'i al."""
        time.sleep(1)
        exit_price, pnl = self._fetch_closed_pnl(pos)
        if exit_price <= 0:
            try:
                exit_price = self.client.fetch_last_price(pos.symbol)
            except Exception:
                exit_price = pos.current_stop
            if pos.side == "long":
                pnl = (exit_price - pos.entry_price) * pos.qty
            else:
                pnl = (pos.entry_price - exit_price) * pos.qty
        return exit_price, pnl

    # ============= CLOSED PNL OKUMA =============
    def _fetch_closed_pnl(self, pos: OpenPosition) -> Tuple[float, float]:
        """Bybit'ten son kapanan PnL'i al ve pozisyonla eşleştir."""
        try:
            records = self.client.fetch_closed_pnl(pos.symbol, limit=5)
        except Exception as e:
            logger.warning(f"{pos.symbol}: closed_pnl alınamadı - {e}")
            return 0.0, 0.0

        if not records:
            return 0.0, 0.0

        opened_ms = int(pos.opened_at * 1000)
        for r in records:
            created = r.get("updatedTime") or r.get("createdTime") or "0"
            try:
                created_ms = int(created)
            except (TypeError, ValueError):
                created_ms = 0
            if created_ms >= opened_ms - 5000:
                try:
                    exit_price = float(r.get("avgExitPrice", 0) or 0)
                    pnl = float(r.get("closedPnl", 0) or 0)
                    return exit_price, pnl
                except (TypeError, ValueError):
                    continue

        r = records[0]
        try:
            exit_price = float(r.get("avgExitPrice", 0) or 0)
            pnl = float(r.get("closedPnl", 0) or 0)
            return exit_price, pnl
        except (TypeError, ValueError):
            return 0.0, 0.0

    # ============= PNL YÜZDESİ =============
    def _compute_pnl_pct(self, pos: OpenPosition, exit_price: float) -> float:
        """Stake üzerinden PnL yüzdesi (kaldıraçlı)."""
        if pos.entry_price <= 0 or pos.stake <= 0:
            return 0.0
        if pos.side == "long":
            price_change = (exit_price - pos.entry_price) / pos.entry_price
        else:
            price_change = (pos.entry_price - exit_price) / pos.entry_price
        return price_change * config.LEVERAGE * 100
