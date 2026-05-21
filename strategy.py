"""
strategy.py - Flag mantigi ve giris sinyali tespiti.

LONG:
  FLAG AC   : prev_price <= ust_ic_tampon  &&  price > ust_ic_tampon (yukari kesti)
  FLAG SIL  : prev_price >= ust_ic_tampon  &&  price < ust_ic_tampon (asagi kesti)
  ISLEM AC  : price > ust_disbant  &&  flag var  &&  acik islem yok

SHORT:
  FLAG AC   : prev_price >= alt_ic_tampon  &&  price < alt_ic_tampon (asagi kesti)
  FLAG SIL  : prev_price <= alt_ic_tampon  &&  price > alt_ic_tampon (yukari kesti)
  ISLEM AC  : price < alt_disbant  &&  flag var  &&  acik islem yok

Onemli: Crossover tespiti icin prev_price gerekli. Yani price_history'de
en az 2 kayit olmali (su anki taramadaki fiyat eklenmeden once,
onceki taramadan kalma fiyat olmali).
"""

from dataclasses import dataclass
from typing import Optional

from bands import Bands


# Flag aksiyon kodlari
FLAG_OPEN_LONG   = "FLAG_OPEN_LONG"
FLAG_OPEN_SHORT  = "FLAG_OPEN_SHORT"
FLAG_CLEAR_LONG  = "FLAG_CLEAR_LONG"
FLAG_CLEAR_SHORT = "FLAG_CLEAR_SHORT"


@dataclass
class EntrySignal:
    side: str   # "LONG" / "SHORT"
    price: float
    bands: Bands


def detect_flag_action(
    prev_price: Optional[float],
    price: float,
    bands: Bands,
    current_flag: Optional[str],
) -> Optional[str]:
    """
    Flag actma veya silme aksiyonu var mi? Yoksa None.

    Donus degeri:
      FLAG_OPEN_LONG   : Long flag acilmali
      FLAG_OPEN_SHORT  : Short flag acilmali
      FLAG_CLEAR_LONG  : Mevcut long flag silinmeli
      FLAG_CLEAR_SHORT : Mevcut short flag silinmeli
      None             : aksiyon yok
    """
    if prev_price is None:
        return None  # prev yoksa crossover tespiti yapilamaz

    # --- LONG flag actma ---
    # prev iç tamponun altinda veya ustunde, su anki ustunde -> yukari kesmis
    if prev_price <= bands.ust_ic_tampon and price > bands.ust_ic_tampon:
        if current_flag != "LONG":
            return FLAG_OPEN_LONG

    # --- LONG flag silme ---
    # Yukaridan asagi kesmis
    if prev_price >= bands.ust_ic_tampon and price < bands.ust_ic_tampon:
        if current_flag == "LONG":
            return FLAG_CLEAR_LONG

    # --- SHORT flag actma ---
    # Yukaridan asagi kesmis (alt ic tamponu)
    if prev_price >= bands.alt_ic_tampon and price < bands.alt_ic_tampon:
        if current_flag != "SHORT":
            return FLAG_OPEN_SHORT

    # --- SHORT flag silme ---
    # Asagidan yukari kesmis
    if prev_price <= bands.alt_ic_tampon and price > bands.alt_ic_tampon:
        if current_flag == "SHORT":
            return FLAG_CLEAR_SHORT

    return None


def detect_entry(
    price: float,
    bands: Bands,
    current_flag: Optional[str],
) -> Optional[EntrySignal]:
    """
    Giris sinyali var mi?

    LONG sinyal: fiyat ust disbantin ustunde VE long flag aktif
    SHORT sinyal: fiyat alt disbantin altinda VE short flag aktif

    Flag yoksa sinyal de yok.
    """
    if current_flag == "LONG" and price > bands.ust_disbant:
        return EntrySignal(side="LONG", price=price, bands=bands)

    if current_flag == "SHORT" and price < bands.alt_disbant:
        return EntrySignal(side="SHORT", price=price, bands=bands)

    return None
