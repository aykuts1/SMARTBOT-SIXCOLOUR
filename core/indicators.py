"""İndikatörler: EMA, Donchian Channel, ATR.

Tüm fonksiyonlar pandas DataFrame veya numpy array kabul eder.
Bybit kline formatı: [timestamp, open, high, low, close, volume, turnover].
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def klines_to_df(klines: list) -> pd.DataFrame:
    """Bybit V5 kline listesini DataFrame'e çevirir.

    Bybit V5 API kline'ları DESC (en yeni en başta) döner.
    Burada ASC (en eski en başta) sıralayıp döneriz.
    """
    if not klines:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"]
        )
    df = pd.DataFrame(
        klines,
        columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
    )
    df["timestamp"] = pd.to_numeric(df["timestamp"]).astype("int64")
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col]).astype("float64")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def ema(values: pd.Series | np.ndarray, period: int) -> pd.Series:
    """Klasik EMA. İlk değer = SMA(period), sonrası recursive.

    period değerinden az veri varsa NaN döner (ya da SMA'den başlar — burada
    pandas ewm kullanıyoruz; adjust=False ile recursive EMA verir).
    """
    s = pd.Series(values).astype(float).reset_index(drop=True)
    return s.ewm(span=period, adjust=False).mean()


def donchian(
    high: pd.Series | np.ndarray,
    low: pd.Series | np.ndarray,
    period: int,
) -> tuple[pd.Series, pd.Series]:
    """Donchian üst (period boyunca yüksek high) ve alt (period boyunca düşük low).

    Standart Donchian: rolling max/min, mevcut mum dahil.
    """
    h = pd.Series(high).astype(float).reset_index(drop=True)
    l = pd.Series(low).astype(float).reset_index(drop=True)
    upper = h.rolling(window=period, min_periods=period).max()
    lower = l.rolling(window=period, min_periods=period).min()
    return upper, lower


def atr(
    high: pd.Series | np.ndarray,
    low: pd.Series | np.ndarray,
    close: pd.Series | np.ndarray,
    period: int = 14,
) -> pd.Series:
    """Wilder's ATR. True Range'ın Wilder smoothing'i (alpha=1/period)."""
    h = pd.Series(high).astype(float).reset_index(drop=True)
    l = pd.Series(low).astype(float).reset_index(drop=True)
    c = pd.Series(close).astype(float).reset_index(drop=True)
    prev_close = c.shift(1)
    tr = pd.concat(
        [
            (h - l).abs(),
            (h - prev_close).abs(),
            (l - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_all(df: pd.DataFrame, ema_period: int, donchian_period: int,
                atr_period: int = 14) -> pd.DataFrame:
    """DataFrame'e ema, donchian_upper, donchian_lower, atr ekler."""
    out = df.copy()
    out["ema"] = ema(out["close"], ema_period)
    out["donchian_upper"], out["donchian_lower"] = donchian(
        out["high"], out["low"], donchian_period
    )
    out["atr"] = atr(out["high"], out["low"], out["close"], atr_period)
    return out
