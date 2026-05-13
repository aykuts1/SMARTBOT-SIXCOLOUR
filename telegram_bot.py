"""
Pozisyon yöneticisi: Açık pozisyonların state'i, stop taşıma, CE trailing,
RSI dönüş çıkışı, çıkış kararı.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import config
import strategy
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
    initial_stop: float
    current_stop: float
    entry_atr: float
    ce_level: float
    running_high: float
    running_low: float
    stake: float
    opened_at: float

    # RSI çıkış için
    rsi_long_threshold: float
    rsi_short_threshold: float
    rsi_flag_set: bool = False    # Eşik aşıldı mı? (long: short eşiği aşıldı, short: long eşiği)

    # Flag'ler
    be_moved: bool = False


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

    # ============= STATE =============
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
        rsi_long_th: float,
        rsi_short_th: float,
    ) -> Optional[OpenPosition]:
        """Limit emir (market gibi) ile pozisyon aç."""
        if entry_atr <= 0:
            logger.warning(f"{symbol}: entry_atr 0, pozisyon açılmayacak")
            return None

        try:
            last_price = self.client.fetch_last_price(symbol)
        except Exception as e:
            logger.error(f"{symbol}: fiyat alınamadı - {e}")
            self.notifier.send(tg_fmt.fmt_error(f"{symbol} fiyat", str(e)))
            return None

        if side == "long":
            limit_price = last_price * (1 + config.LIMIT_SLIPPAGE)
            limit_price = self.client.round_price(symbol, limit_price, round_up=True)
        else:
            limit_price = last_price * (1 - config.LIMIT_SLIPPAGE)
            limit_price = self.client.round_price(symbol, limit_price, round_up=False)

        notional = self.stake * config.LEVERAGE
        raw_qty = notional / limit_price
        qty = self.client.round_qty(symbol, raw_qty)

        info = self.client.fetch_instrument_info(symbol)
        if qty < info["min_qty"]:
            logger.warning(f"{symbol}: qty {qty} < min_qty {info['min_qty']}, atlanıyor")
            return None
        if qty <= 0:
            logger.warning(f"{symbol}: qty 0, atlanıyor")
            return None

        bybit_side = "Buy" if side == "long" else "Sell"
        try:
            self.client.place_limit_order(
                symbol=symbol, side=bybit_side,
                qty=qty, price=limit_price, reduce_only=False,
            )
        except Exception as e:
            logger.error(f"{symbol}: emir hatası - {e}")
            self.notifier.send(tg_fmt.fmt_error(f"{symbol} entry order", str(e)))
            return None

        filled_position = self._wait_for_fill(symbol, max_wait=15)
        if filled_position is None:
            logger.warning(f"{symbol}: emir dolmadı, iptal ediliyor")
            self.client.cancel_all_orders(symbol)
            return None

        try:
            real_entry = float(filled_position.get("avgPrice", limit_price))
        except (TypeError, ValueError):
            real_entry = limit_price
        try:
            real_qty = float(filled_position.get("size", qty))
        except (TypeError, ValueError):
            real_qty = qty

        # %1 stop hesapla
        if side == "long":
            stop_price = real_entry * (1 - config.INITIAL_STOP_PERCENT)
            stop_price = self.client.round_price(symbol, stop_price, round_up=False)
            initial_ce = real_entry - config.CE_INITIAL_MULTIPLIER * entry_atr
        else:
            stop_price = real_entry * (1 + config.INITIAL_STOP_PERCENT)
            stop_price = self.client.round_price(symbol, stop_price, round_up=True)
            initial_ce = real_entry + config.CE_INITIAL_MULTIPLIER * entry_atr

        try:
            self.client.set_trading_stop(symbol=symbol, stop_loss=stop_price)
        except Exception as e:
            logger.error(f"{symbol}: stop set hatası - {e}")
            self.notifier.send(tg_fmt.fmt_error(f"{symbol} stop set", str(e)))
            try:
                close_side = "Sell" if side == "long" else "Buy"
                self.client.place_market_order(symbol, close_side, real_qty, reduce_only=True)
            except Exception as e2:
                logger.error(f"{symbol}: acil kapatma da başarısız - {e2}")
            return None

        pos = OpenPosition(
            symbol=symbol,
            side=side,
            entry_price=real_entry,
            qty=real_qty,
            initial_stop=stop_price,
            current_stop=stop_price,
            entry_atr=entry_atr,
            ce_level=initial_ce,
            running_high=real_entry,
            running_low=real_entry,
            stake=self.stake,
            opened_at=time.time(),
            rsi_long_threshold=rsi_long_th,
            rsi_short_threshold=rsi_short_th,
            rsi_flag_set=False,
        )
        self._add(pos)
        return pos

    def _wait_for_fill(self, symbol: str, max_wait: int = 15) -> Optional[Dict]:
        """Limit emrin dolmasını bekle."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                p = self.client.fetch_position(symbol)
                if p:
                    return p
            except Exception as e:
                logger.warning(f"{symbol}: fill bekleme hata - {e}")
            time.sleep(1)
        return None

    # ============= RSI EŞİK GÜNCELLEME (30dk taramada çağrılır) =============
    def update_rsi_thresholds(
        self, symbol: str, long_th: float, short_th: float, current_rsi: float
    ) -> None:
        """
        30dk taramada açık pozisyonun RSI eşiklerini güncelle.
        Flag'i yeniden değerlendir:
          - Long için: RSI hâlâ yeni short_th'i aşıyorsa flag korunur, gerisindeyse sıfırlanır
          - Short için: RSI hâlâ yeni long_th'in altındaysa flag korunur, üstündeyse sıfırlanır
        """
        with self._lock:
            pos = self._positions.get(symbol)
            if not pos:
                return
            pos.rsi_long_threshold = long_th
            pos.rsi_short_threshold = short_th

            if pos.rsi_flag_set:
                if pos.side == "long":
                    if current_rsi < short_th:
                        pos.rsi_flag_set = False
                        logger.info(f"{symbol}: RSI flag sıfırlandı (RSI {current_rsi:.2f} < {short_th:.2f})")
                else:
                    if current_rsi > long_th:
                        pos.rsi_flag_set = False
                        logger.info(f"{symbol}: RSI flag sıfırlandı (RSI {current_rsi:.2f} > {long_th:.2f})")

    # ============= ÇIKIŞ TARAMASI =============
    def scan_exits(
        self,
        kline_fetcher: Callable,
    ) -> List[Tuple[OpenPosition, str, float, float, float]]:
        """
        Her açık pozisyon için kontroller:
          1. Borsa'da hâlâ açık mı? (SL tetiklendiyse çıkış)
          2. BE taşıma (0.7 ATR kâr)
          3. CE trailing güncellemesi
          4. CE tetik kontrolü
          5. RSI dönüş çıkışı

        Returns: kapanan pozisyonlar [(pos, reason, exit_price, pnl_usdt, pnl_pct), ...]
        """
        closed = []
        be_moves: List[OpenPosition] = []

        for pos in self.get_all():
            try:
                bybit_pos = self.client.fetch_position(pos.symbol)
            except Exception as e:
                logger.warning(f"{pos.symbol}: position fetch hata - {e}")
                continue

            # SL tetiklendi
            if bybit_pos is None:
                exit_price, pnl_usdt = self._handle_external_close(pos)
                pnl_pct = self._compute_pnl_pct(pos, exit_price)
                self._remove(pos.symbol)
                self.client.cancel_all_orders(pos.symbol)
                closed.append((pos, "Stop Loss tetiklendi", exit_price, pnl_usdt, pnl_pct))
                continue

            # Anlık fiyat
            try:
                last_price = self.client.fetch_last_price(pos.symbol)
            except Exception as e:
                logger.warning(f"{pos.symbol}: fiyat alınamadı - {e}")
                continue

            # Running high/low
            if last_price > pos.running_high:
                pos.running_high = last_price
            if last_price < pos.running_low:
                pos.running_low = last_price

            # BE taşıma
            be_moved_now = self._maybe_move_to_be(pos, last_price)
            if be_moved_now:
                be_moves.append(pos)

            # CE seviyesini güncelle
            self._update_ce_level(pos)

            # 1) CE tetik kontrolü
            if self._check_ce_hit(pos, last_price):
                exit_price, pnl_usdt = self._close_position(pos, "CE")
                if exit_price > 0:
                    pnl_pct = self._compute_pnl_pct(pos, exit_price)
                    self._remove(pos.symbol)
                    closed.append((pos, "Chandelier Exit tetiklendi", exit_price, pnl_usdt, pnl_pct))
                continue

            # 2) RSI dönüş çıkışı
            try:
                df = kline_fetcher(pos.symbol)
            except Exception as e:
                logger.warning(f"{pos.symbol}: RSI kline hata - {e}")
                df = None

            if df is not None:
                rsi_exit = self._check_rsi_exit(pos, df)
                if rsi_exit:
                    exit_price, pnl_usdt = self._close_position(pos, "RSI")
                    if exit_price > 0:
                        pnl_pct = self._compute_pnl_pct(pos, exit_price)
                        self._remove(pos.symbol)
                        closed.append((pos, "RSI dönüş crossover", exit_price, pnl_usdt, pnl_pct))

        # BE bildirimlerini gönder (kapanmayan pozisyonlar için)
        for pos in be_moves:
            if self.has(pos.symbol):  # hâlâ açık
                self.notifier.send(tg_fmt.fmt_be_moved(
                    symbol=pos.symbol,
                    side=pos.side,
                    new_stop=pos.current_stop,
                    entry_price=pos.entry_price,
                ))

        return closed

    # ============= BE TAŞIMA =============
    def _maybe_move_to_be(self, pos: OpenPosition, last_price: float) -> bool:
        """0.7 ATR kârda stop'u entry + 0.2 ATR'ye taşı. Taşındıysa True döner."""
        if pos.be_moved:
            return False

        trigger_distance = config.BE_TRIGGER_ATR * pos.entry_atr

        if pos.side == "long":
            profit = last_price - pos.entry_price
            if profit < trigger_distance:
                return False
            new_stop = pos.entry_price + config.BE_OFFSET_ATR * pos.entry_atr
            new_stop = self.client.round_price(pos.symbol, new_stop, round_up=False)
            if new_stop <= pos.current_stop:
                pos.be_moved = True
                return False
        else:
            profit = pos.entry_price - last_price
            if profit < trigger_distance:
                return False
            new_stop = pos.entry_price - config.BE_OFFSET_ATR * pos.entry_atr
            new_stop = self.client.round_price(pos.symbol, new_stop, round_up=True)
            if new_stop >= pos.current_stop:
                pos.be_moved = True
                return False

        try:
            self.client.set_trading_stop(pos.symbol, stop_loss=new_stop)
            pos.current_stop = new_stop
            pos.be_moved = True
            logger.info(f"{pos.symbol}: stop BE'ye taşındı → {new_stop}")
            return True
        except Exception as e:
            logger.warning(f"{pos.symbol}: BE taşıma hata - {e}")
            return False

    # ============= CE TRAILING =============
    def _update_ce_level(self, pos: OpenPosition) -> None:
        """CE = running_high - 1×ATR (long), running_low + 1×ATR (short). Sadece lehte hareket."""
        if pos.side == "long":
            new_ce = pos.running_high - config.CE_INITIAL_MULTIPLIER * pos.entry_atr
            if new_ce > pos.ce_level:
                pos.ce_level = new_ce
        else:
            new_ce = pos.running_low + config.CE_INITIAL_MULTIPLIER * pos.entry_atr
            if new_ce < pos.ce_level:
                pos.ce_level = new_ce

    def _check_ce_hit(self, pos: OpenPosition, last_price: float) -> bool:
        if pos.side == "long":
            return last_price < pos.ce_level
        return last_price > pos.ce_level

    # ============= RSI DÖNÜŞ ÇIKIŞI =============
    def _check_rsi_exit(self, pos: OpenPosition, df) -> bool:
        """
        Anlık RSI hesapla, eşik mantığını uygula.
        Long: RSI short eşiğini aştı mı? Aştıktan sonra geri döndü mü?
        Short: RSI long eşiğinin altına indi mi? İndikten sonra geri çıktı mı?
        """
        result = strategy.compute_rsi_and_thresholds(df)
        if result is None:
            return False
        current_rsi, _, _ = result

        if pos.side == "long":
            threshold = pos.rsi_short_threshold
            if not pos.rsi_flag_set:
                if current_rsi >= threshold:
                    pos.rsi_flag_set = True
                    logger.info(f"{pos.symbol}: RSI flag set (RSI {current_rsi:.2f} >= {threshold:.2f})")
                return False
            else:
                if current_rsi < threshold:
                    logger.info(f"{pos.symbol}: RSI çıkış (RSI {current_rsi:.2f} < {threshold:.2f})")
                    return True
                return False
        else:
            threshold = pos.rsi_long_threshold
            if not pos.rsi_flag_set:
                if current_rsi <= threshold:
                    pos.rsi_flag_set = True
                    logger.info(f"{pos.symbol}: RSI flag set (RSI {current_rsi:.2f} <= {threshold:.2f})")
                return False
            else:
                if current_rsi > threshold:
                    logger.info(f"{pos.symbol}: RSI çıkış (RSI {current_rsi:.2f} > {threshold:.2f})")
                    return True
                return False

    # ============= POZİSYON KAPATMA =============
    def _close_position(self, pos: OpenPosition, reason_short: str) -> Tuple[float, float]:
        """Market gibi limit emirle pozisyonu kapat."""
        try:
            last_price = self.client.fetch_last_price(pos.symbol)
        except Exception as e:
            logger.error(f"{pos.symbol}: kapanış fiyat alınamadı - {e}")
            return 0.0, 0.0

        try:
            self.client.cancel_all_orders(pos.symbol)
        except Exception:
            pass

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
                symbol=pos.symbol, side=close_side,
                qty=pos.qty, price=limit_price, reduce_only=True,
            )
        except Exception as e:
            logger.error(f"{pos.symbol}: {reason_short} kapanış limit hata - {e}")
            try:
                self.client.place_market_order(pos.symbol, close_side, pos.qty, reduce_only=True)
            except Exception as e2:
                logger.error(f"{pos.symbol}: {reason_short} kapanış market hata - {e2}")
                self.notifier.send(tg_fmt.fmt_error(f"{pos.symbol} {reason_short} close", str(e2)))
                return 0.0, 0.0

        time.sleep(3)

        exit_price, pnl = self._fetch_closed_pnl(pos)
        if exit_price <= 0:
            exit_price = last_price
            if pos.side == "long":
                pnl = (exit_price - pos.entry_price) * pos.qty
            else:
                pnl = (pos.entry_price - exit_price) * pos.qty
        return exit_price, pnl

    def _handle_external_close(self, pos: OpenPosition) -> Tuple[float, float]:
        """SL tetiklendi → closed PnL'i al."""
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

    def _fetch_closed_pnl(self, pos: OpenPosition) -> Tuple[float, float]:
        """Bybit'ten son kapanan PnL'i al."""
        try:
            records = self.client.fetch_closed_pnl(pos.symbol, limit=5)
        except Exception as e:
            logger.warning(f"{pos.symbol}: closed_pnl alınamadı - {e}")
            return 0.0, 0.0

        if not records:
            return 0.0, 0.0

        opened_ms = int(pos.opened_at * 1000)
        # Pozisyon açılış zamanından sonra kapanmış olan ilk kaydı bul
        for r in records:
            updated = r.get("updatedTime") or r.get("createdTime") or "0"
            try:
                updated_ms = int(updated)
            except (TypeError, ValueError):
                updated_ms = 0
            if updated_ms >= opened_ms - 5000:
                try:
                    exit_price = float(r.get("avgExitPrice", 0) or 0)
                    pnl = float(r.get("closedPnl", 0) or 0)
                    if exit_price > 0:
                        return exit_price, pnl
                except (TypeError, ValueError):
                    continue

        # Fallback: ilk kayıt
        r = records[0]
        try:
            exit_price = float(r.get("avgExitPrice", 0) or 0)
            pnl = float(r.get("closedPnl", 0) or 0)
            return exit_price, pnl
        except (TypeError, ValueError):
            return 0.0, 0.0

    def _compute_pnl_pct(self, pos: OpenPosition, exit_price: float) -> float:
        """Stake üzerinden PnL yüzdesi (kaldıraçlı)."""
        if pos.entry_price <= 0 or pos.stake <= 0:
            return 0.0
        if pos.side == "long":
            price_change = (exit_price - pos.entry_price) / pos.entry_price
        else:
            price_change = (pos.entry_price - exit_price) / pos.entry_price
        return price_change * config.LEVERAGE * 100
