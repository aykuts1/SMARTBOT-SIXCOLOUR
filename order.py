"""
order.py - Emir gonderme sistemi.

Giris emri:
  - Anlik fiyat - 1 tick (long) / + 1 tick (short)
  - Post-only, ATTEMPT_WAIT_SEC bekle, dolmadiysa iptal -> yeni fiyatla tekrar
  - Max ENTRY_ATTEMPTS deneme
  - Dolmazsa: sinyal atlanir (market emir YOK)

Cikis emri:
  - Anlik fiyat + 1 tick (long cikis = Sell) / - 1 tick (short cikis = Buy)
  - Post-only, ATTEMPT_WAIT_SEC bekle, dolmadiysa iptal -> yeni fiyatla tekrar
  - Max EXIT_ATTEMPTS deneme
  - Dolmazsa: MARKET emir ile kapatilir (tek istisna)
"""

import math
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class FillResult:
    filled:        bool
    avg_price:     float = 0.0
    qty:           float = 0.0
    attempts:      int = 0
    last_order_id: Optional[str] = None
    last_price:    float = 0.0
    market_fallback: bool = False   # Cikiste market emirle kapandi mi?


# ---------------------------------------------------------------------------
# Yuvarlama yardimcilari
# ---------------------------------------------------------------------------

def round_step(value: float, step: float) -> float:
    """Bybit kurali: tickSize/qtyStep katlari, floor."""
    if step <= 0:
        return value
    return math.floor(value / step) * step


def _step_decimals(step: float) -> int:
    if step >= 1:
        return 0
    return max(0, -int(math.floor(math.log10(step))))


def fmt_price(price: float, tick: float) -> str:
    p = round_step(price, tick)
    return f"{p:.{_step_decimals(tick)}f}"


def fmt_qty(qty: float, step: float) -> str:
    q = round_step(qty, step)
    return f"{q:.{_step_decimals(step)}f}"


def calc_qty(notional_usdt: float, price: float, qty_step: float, min_qty: float) -> float:
    """notional = stake * leverage. Miktari coin cinsinden dondurur."""
    if price <= 0:
        return 0.0
    raw = notional_usdt / price
    rounded = round_step(raw, qty_step)
    if rounded < min_qty:
        return 0.0
    return rounded


# ---------------------------------------------------------------------------
# Limit emir dongusu (giris ve cikis ortak)
# ---------------------------------------------------------------------------

def _try_limit_loop(
    bybit_client,
    symbol: str,
    bybit_side: str,    # "Buy" / "Sell"
    qty: float,
    instrument: dict,
    max_attempts: int,
    wait_seconds: int,
    reduce_only: bool,
    on_attempt_fail: Optional[Callable[[int, str], None]] = None,
) -> FillResult:
    """
    Anlik fiyatin 1 tick alti/ustune post-only limit emir.
    Dolmadiysa iptal eder, yeni fiyatla tekrar dener. max_attempts kez.
    """
    tick = instrument["tick_size"]
    qty_step = instrument["qty_step"]
    qty_str = fmt_qty(qty, qty_step)

    result = FillResult(filled=False)

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt

        # 1. Anlik fiyati al
        try:
            last_price = bybit_client.fetch_last_price(symbol)
        except Exception as e:
            if on_attempt_fail:
                on_attempt_fail(attempt, f"fetch_last_price hatasi: {e}")
            time.sleep(1)
            continue

        # 2. Emir fiyatini hesapla (1 tick uzakta, PostOnly garantisi)
        if bybit_side == "Buy":
            price = last_price - tick
        else:
            price = last_price + tick
        price_str = fmt_price(price, tick)
        result.last_price = float(price_str)

        # 3. Emri gonder
        try:
            r = bybit_client.place_limit_post_only(
                symbol=symbol,
                side=bybit_side,
                qty=qty_str,
                price=price_str,
                reduce_only=reduce_only,
            )
            order_id = r.get("orderId")
            result.last_order_id = order_id
        except Exception as e:
            if on_attempt_fail:
                on_attempt_fail(attempt, f"place_order hatasi: {e}")
            time.sleep(1)
            continue

        # 4. Bekle
        time.sleep(wait_seconds)

        # 5. Emir durumunu kontrol et
        try:
            order = bybit_client.get_order(symbol, order_id)
        except Exception:
            order = None

        if order is not None:
            status = (order.get("orderStatus") or "").lower()
            cum_qty = float(order.get("cumExecQty") or 0)
            if status in ("filled",) and cum_qty > 0:
                avg = float(order.get("avgPrice") or price_str)
                result.filled = True
                result.avg_price = avg
                result.qty = cum_qty
                return result

        # 6. Dolmadi -> iptal et ve tekrar dene
        if result.last_order_id:
            bybit_client.cancel_order(symbol, result.last_order_id)
        if on_attempt_fail:
            on_attempt_fail(attempt, "dolmadi")

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def submit_entry(
    bybit_client,
    symbol: str,
    side: str,        # "LONG" / "SHORT"
    qty: float,
    instrument: dict,
    max_attempts: int,
    wait_seconds: int,
    on_attempt_fail: Optional[Callable[[int, str], None]] = None,
) -> FillResult:
    """Giris emri. Dolmadiysa filled=False doner, sinyal atlanmali."""
    bybit_side = "Buy" if side == "LONG" else "Sell"
    return _try_limit_loop(
        bybit_client=bybit_client,
        symbol=symbol,
        bybit_side=bybit_side,
        qty=qty,
        instrument=instrument,
        max_attempts=max_attempts,
        wait_seconds=wait_seconds,
        reduce_only=False,
        on_attempt_fail=on_attempt_fail,
    )


def submit_exit(
    bybit_client,
    symbol: str,
    side: str,        # pozisyonun yonu ("LONG" / "SHORT")
    qty: float,
    instrument: dict,
    max_attempts: int,
    wait_seconds: int,
    on_attempt_fail: Optional[Callable[[int, str], None]] = None,
) -> FillResult:
    """
    Cikis emri. Limit denemeleri dolmadiysa MARKET emir ile kapatilir.
    Tek market emir istisnasi burasidir.
    """
    # Cikis emri yonu ters: LONG icin Sell, SHORT icin Buy
    bybit_side = "Sell" if side == "LONG" else "Buy"

    result = _try_limit_loop(
        bybit_client=bybit_client,
        symbol=symbol,
        bybit_side=bybit_side,
        qty=qty,
        instrument=instrument,
        max_attempts=max_attempts,
        wait_seconds=wait_seconds,
        reduce_only=True,
        on_attempt_fail=on_attempt_fail,
    )

    if result.filled:
        return result

    # --- Fallback: market emir ---
    qty_str = fmt_qty(qty, instrument["qty_step"])
    try:
        r = bybit_client.place_market_order(
            symbol=symbol,
            side=bybit_side,
            qty=qty_str,
            reduce_only=True,
        )
        # Market emirden hemen sonra son fiyati al
        try:
            mp = bybit_client.fetch_last_price(symbol)
        except Exception:
            mp = result.last_price or 0.0

        result.filled = True
        result.market_fallback = True
        result.avg_price = mp
        result.qty = qty
        result.last_order_id = r.get("orderId")
    except Exception as e:
        # Market emir bile gonderilemediyse exception firlat
        raise RuntimeError(f"Cikis market emir basarisiz: {e}")

    return result
