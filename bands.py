"""
bands.py - EMA, ATR ve bant cizgilerini hesaplar.

Band yapisi:
  Ust Dis Tampon  = Ust Disbant + BUFFER * ATR     <- LONG BE tetik
  Ust Disbant     = EMA + BAND * ATR               <- LONG giris
  Ust Ic Tampon   = Ust Disbant - BUFFER * ATR     <- LONG flag

  EMA (orta cizgi)

  Alt Ic Tampon   = Alt Disbant + BUFFER * ATR     <- SHORT flag
  Alt Disbant     = EMA - BAND * ATR               <- SHORT giris
  Alt Dis Tampon  = Alt Disbant - BUFFER * ATR     <- SHORT BE tetik
"""

from dataclasses import dataclass
from typing import List


@dataclass
class Bands:
    """Hesaplanmis bant degerleri."""
    ema:            float
    atr:            float

    ust_disbant:    float  # giris
    ust_ic_tampon:  float  # flag
    ust_dis_tampon: float  # BE tetik

    alt_disbant:    float
    alt_ic_tampon:  float
    alt_dis_tampon: float


def _ema(values: List[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"EMA icin yeterli veri yok: {len(values)} < {period}")
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int) -> float:
    """Wilder's ATR."""
    if len(highs) < period + 1:
        raise ValueError(f"ATR icin yeterli veri yok: {len(highs)} < {period + 1}")
    trs = []
    for i in range(1, len(highs)):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_bands(
    klines: List[List],
    ema_period: int,
    atr_period: int,
    band_multiplier: float,
    buffer_multiplier: float,
) -> Bands:
    """
    Kronolojik sirali (eski->yeni) kline listesinden bant degerlerini hesaplar.
    Bybit kline formati: [timestamp, open, high, low, close, volume, turnover]
    """
    min_needed = max(ema_period, atr_period) + 2
    if len(klines) < min_needed:
        raise ValueError(f"Yeterli mum yok: {len(klines)} < {min_needed}")

    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]

    ema = _ema(closes, ema_period)
    atr = _atr(highs, lows, closes, atr_period)

    band_offset = atr * band_multiplier
    buf_offset  = atr * buffer_multiplier

    ust_disbant    = ema + band_offset
    alt_disbant    = ema - band_offset

    ust_ic_tampon  = ust_disbant - buf_offset
    ust_dis_tampon = ust_disbant + buf_offset

    alt_ic_tampon  = alt_disbant + buf_offset
    alt_dis_tampon = alt_disbant - buf_offset

    return Bands(
        ema=ema,
        atr=atr,
        ust_disbant=ust_disbant,
        ust_ic_tampon=ust_ic_tampon,
        ust_dis_tampon=ust_dis_tampon,
        alt_disbant=alt_disbant,
        alt_ic_tampon=alt_ic_tampon,
        alt_dis_tampon=alt_dis_tampon,
    )


def sort_klines_chronological(klines: List[List]) -> List[List]:
    """Bybit yanitini timestamp'e gore artan siralar (eski -> yeni)."""
    return sorted(klines, key=lambda k: int(k[0]))
