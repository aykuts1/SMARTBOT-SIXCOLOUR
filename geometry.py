"""
geometry.py — ORTAK TABLO GEOMETRİSİ (yeni tasarım)

Tüm "primary" tablolar bu modülü kullanır:
  - Kırmızı, Beyaz        (ana thread'ler, winrate_zones = 5)
  - Sarı 1, Turuncu 1     (winrate_zones = 4)
  - Sarı 2, Turuncu 2     (winrate_zones = 3)

SHORT için (E = giriş fiyatı, yukarıdan aşağıya):

    LOSE      = E + LZ
    LS3       = E + 0.75 * LZ
    LS2       = E + 0.50 * LZ
    LS1       = E + 0.25 * LZ
    ENTRY     = E
    LS_TAMPON = E - 0.25 * LZ        (ENTRY bölgesinin üst dilimi)
    ST1       = E - 1 * LZ
    ST2       = E - 2 * LZ
    ...
    ST(n-1)   = E - (n-1) * LZ
    WINRATE   = E - n * LZ           (n = winrate_zones)

LONG = tam simetrik (lose_dir = -1).

LZ (LOSE ZONE genişliği):
  - Kırmızı/Beyaz için Donchian'a göre, fiyatın max %2'si ile sınırlı.
  - Sarı/Turuncu tabloları parent'la AYNI LZ'yi kullanır (sadece çapa farklı).

Konum normalizasyonu (zone_position):
    p = lose_dir * (price - E) / LZ
    p = +1  → LOSE çizgisi
    p =  0  → ENTRY çizgisi
    p = -n  → WINRATE çizgisi
"""
import math


def compute_lose_zone(side, entry, donchian_line, max_lose_pct):
    """
    LOSE ZONE genişliğini (pozitif sayı) döndürür.

    SHORT: donchian_line = Donchian ÜST. LOSE = min(üst, E*(1+%)).
    LONG:  donchian_line = Donchian ALT. LOSE = max(alt, E*(1-%)).

    Geçersizse (<= 0) döner; çağıran bunu kontrol etmeli.
    """
    pct = max_lose_pct / 100.0
    if side == "SHORT":
        max_lose = entry * (1.0 + pct)
        lose = min(donchian_line, max_lose)
        lz = lose - entry
    else:
        max_lose = entry * (1.0 - pct)
        lose = max(donchian_line, max_lose)
        lz = entry - lose
    return lz


def build_table(side, entry, lz, winrate_zones):
    """
    entry + LZ'den tüm tablo çizgilerini üretir.
    winrate_zones: ENTRY'nin altındaki ST bölgesi sayısı + 1 (WINRATE dahil mesafe).
      - 5 → ST1..ST4, WINRATE = E - 5*LZ   (Kırmızı/Beyaz)
      - 4 → ST1..ST3, WINRATE = E - 4*LZ   (Sarı1/Turuncu1)
      - 3 → ST1..ST2, WINRATE = E - 3*LZ   (Sarı2/Turuncu2)
    """
    lose_dir = 1.0 if side == "SHORT" else -1.0
    lines = {
        "LOSE": entry + lose_dir * lz,
        "LS3": entry + lose_dir * 0.75 * lz,
        "LS2": entry + lose_dir * 0.50 * lz,
        "LS1": entry + lose_dir * 0.25 * lz,
        "ENTRY": entry,
        "LS_TAMPON": entry - lose_dir * 0.25 * lz,
    }
    for k in range(1, winrate_zones):
        lines["ST%d" % k] = entry - lose_dir * k * lz
    lines["WINRATE"] = entry - lose_dir * winrate_zones * lz
    return lines


def zone_position(side, entry, lz, price):
    """
    Normalize konum p. lz <= 0 ise None döner.
    """
    if lz is None or lz <= 0:
        return None
    lose_dir = 1.0 if side == "SHORT" else -1.0
    return lose_dir * (price - entry) / lz


def classify_zone(p, winrate_zones):
    """
    Konum p'ye göre bölge etiketi:
      None         → tablo dışı (LOSE üstü veya WINRATE altı)
      "LS3/LS2/LS1/LS_ENTRY" → LOSE ZONE dilimleri (ENTRY üstü)
      "ENTRY"      → giriş ile ST1 arası (asıl trade bölgesi)
      "ST1".."STk" → ilgili ST bölgesi (ENTRY altı)
    """
    if p is None:
        return None
    if p > 1.0:
        return None                 # LOSE üstü
    if p > 0.75:
        return "LS3"
    if p > 0.5:
        return "LS2"
    if p > 0.25:
        return "LS1"
    if p > 0.0:
        return "LS_ENTRY"
    # p <= 0 → ENTRY ve ST bölgeleri (giriş altı)
    if p <= -float(winrate_zones):
        return None                 # WINRATE altı
    if p > -1.0:
        return "ENTRY"
    k = int(math.floor(-p))         # p in (-2,-1] → 1, (-3,-2] → 2 ...
    return "ST%d" % k


def find_zone(side, entry, lz, price, winrate_zones):
    """Tek adımda fiyatın bölgesini döndürür."""
    p = zone_position(side, entry, lz, price)
    return classify_zone(p, winrate_zones)
