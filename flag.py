"""
SMARTBOT REDBLUE — flag.py
Flag acma/silme mantigi. Crossover dogrulamasi.
"""

from typing import List


def crossover_up(current_price: float, recent_prices: List[float], line: float) -> bool:
    """
    Yukari yonlu crossover:
    - Guncel fiyat cizginin uzerinde
    - Son N tarama fiyatindan en az biri cizginin altinda olmali
    """
    if current_price <= line:
        return False
    if not recent_prices:
        return False
    return any(p < line for p in recent_prices)


def crossover_down(current_price: float, recent_prices: List[float], line: float) -> bool:
    """
    Asagi yonlu crossover:
    - Guncel fiyat cizginin altinda
    - Son N tarama fiyatindan en az biri cizginin uzerinde olmali
    """
    if current_price >= line:
        return False
    if not recent_prices:
        return False
    return any(p > line for p in recent_prices)


# ──────────────────────────────────────────
# KIRMIZI FLAG MANTIGI
# ──────────────────────────────────────────

def check_kirmizi_long_flag_open(price: float, recent: List[float], bands: dict) -> bool:
    """Kirmizi Ust Ic Tampon yukari kesilirse flag acilir."""
    return crossover_up(price, recent, bands["kirmizi_ust_ictampon"])


def check_kirmizi_long_flag_close(price: float, recent: List[float], bands: dict) -> bool:
    """Kirmizi Ust Ic Tampon asagi kesilir ve altinda kalirsa flag silinir."""
    line = bands["kirmizi_ust_ictampon"]
    return crossover_down(price, recent, line) and price < line


def check_kirmizi_short_flag_open(price: float, recent: List[float], bands: dict) -> bool:
    """Kirmizi Alt Ic Tampon asagi kesilirse flag acilir."""
    return crossover_down(price, recent, bands["kirmizi_alt_ictampon"])


def check_kirmizi_short_flag_close(price: float, recent: List[float], bands: dict) -> bool:
    """Kirmizi Alt Ic Tampon yukari kesilir ve ustunde kalirsa flag silinir."""
    line = bands["kirmizi_alt_ictampon"]
    return crossover_up(price, recent, line) and price > line


# ──────────────────────────────────────────
# MAVI FLAG MANTIGI
# ──────────────────────────────────────────

def check_mavi_long_flag_open(price: float, recent: List[float], bands: dict) -> bool:
    """Mavi Alt Dis Tampon yukari kesilir ve ustunde kalirsa flag acilir."""
    line = bands["mavi_alt_distampon"]
    return crossover_up(price, recent, line) and price > line


def check_mavi_long_flag_close(price: float, recent: List[float], bands: dict) -> bool:
    """Mavi Alt Dis Tampon asagi kesilir ve altinda kalirsa flag silinir."""
    line = bands["mavi_alt_distampon"]
    return crossover_down(price, recent, line) and price < line


def check_mavi_short_flag_open(price: float, recent: List[float], bands: dict) -> bool:
    """Mavi Ust Dis Tampon asagi kesilir ve altinda kalirsa flag acilir."""
    line = bands["mavi_ust_distampon"]
    return crossover_down(price, recent, line) and price < line


def check_mavi_short_flag_close(price: float, recent: List[float], bands: dict) -> bool:
    """Mavi Ust Dis Tampon yukari kesilir ve ustunde kalirsa flag silinir."""
    line = bands["mavi_ust_distampon"]
    return crossover_up(price, recent, line) and price > line


# ──────────────────────────────────────────
# GIRIS KOSULLARI
# ──────────────────────────────────────────

def check_kirmizi_long_entry(price: float, recent: list, bands: dict) -> bool:
    """
    Fiyat Kirmizi Ust Dis Cizgi ustunde VE
    son 3 taramadan en az biri cizginin altinda olmali (crossover).
    """
    line = bands["kirmizi_ust_disticizgi"]
    if price <= line:
        return False
    # Son 3 fiyattan en az biri altinda olmali
    last3 = recent[-3:] if len(recent) >= 3 else recent
    if not last3:
        return False
    return any(p < line for p in last3)


def check_kirmizi_short_entry(price: float, recent: list, bands: dict) -> bool:
    """
    Fiyat Kirmizi Alt Dis Cizgi altinda VE
    son 3 taramadan en az biri cizginin ustunde olmali (crossover).
    """
    line = bands["kirmizi_alt_disticizgi"]
    if price >= line:
        return False
    last3 = recent[-3:] if len(recent) >= 3 else recent
    if not last3:
        return False
    return any(p > line for p in last3)


def check_mavi_long_entry(price: float, recent: list, bands: dict) -> bool:
    """
    Fiyat Mavi Alt Dis Cizgi ustunde VE
    son 3 taramadan en az biri cizginin altinda olmali (crossover).
    """
    line = bands["mavi_alt_disticizgi"]
    if price <= line:
        return False
    last3 = recent[-3:] if len(recent) >= 3 else recent
    if not last3:
        return False
    return any(p < line for p in last3)


def check_mavi_short_entry(price: float, recent: list, bands: dict) -> bool:
    """
    Fiyat Mavi Ust Dis Cizgi altinda VE
    son 3 taramadan en az biri cizginin ustunde olmali (crossover).
    """
    line = bands["mavi_ust_disticizgi"]
    if price >= line:
        return False
    last3 = recent[-3:] if len(recent) >= 3 else recent
    if not last3:
        return False
    return any(p > line for p in last3)
