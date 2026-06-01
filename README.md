# 🤖 SMARTBOT SIXCOLOUR

Bybit Futures üzerinde **12 coin**, **50x kaldıraç**, **hedge mode** ile çalışan
otomatik strateji botu. İki bağımsız **ekosistem**, toplam **6 paralel thread**.

İki ekosistem birbirinden tamamen bağımsız çalışır. Aynı coinde aynı anda
1 Kırmızı ekosistemi + 1 Beyaz ekosistemi açık olabilir.

- 🔴 **KIRMIZI EKOSİSTEMİ**: Kırmızı + Mavi + Sarı 1 + Mavi 1 + Sarı 2 + Mavi 2
- ⚪️ **BEYAZ EKOSİSTEMİ**: Beyaz + Mor + Turuncu 1 + Mor 1 + Turuncu 2 + Mor 2

Her ekosistem aynı anda en fazla **6 işlem** açar → toplamda coin başına **12 işlem**.

---

## 🚀 Kurulum

### Environment variables
```
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### Komutlar
```bash
pip install -r requirements.txt
python main.py
```

Railway için `Procfile` ve `runtime.txt` hazırdır.

### Coin Listesi (12)
BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, TRXUSDT, HYPEUSDT, DOGEUSDT,
AVAXUSDT, NEARUSDT, ADAUSDT, ATOMUSDT

---

## 📐 Tablo Geometrisi (yeni — `geometry.py`)

Bir işlem açıldığında SABİT bir tablo kurulur. SHORT için (E = giriş fiyatı):

```
─────  LOSE          = E + LZ
       LS3 bölgesi
─────  LS3           = E + 0.75·LZ
       LS2 bölgesi
─────  LS2           = E + 0.50·LZ
       LS1 bölgesi          ← hedge bu bölgede açılır
─────  LS1           = E + 0.25·LZ
       LS ENTRY bölgesi
─────  ENTRY (giriş) = E
       LS TAMPON / ENTRY üst dilim
─────  LS TAMPON     = E − 0.25·LZ      ← hedge altına geçince KÂR kapanış
       ENTRY bölgesi
─────  ST1           = E − 1·LZ         ← Sarı 1 burada doğar
       ST1 bölgesi
─────  ST2           = E − 2·LZ         ← Sarı 2 burada doğar
       ST2 bölgesi
─────  ST3           = E − 3·LZ
─────  ST4           = E − 4·LZ
─────  WINRATE       = E − 5·LZ
```

- **LZ (LOSE ZONE genişliği)** = Donchian'a göre, fiyatın **max %2'si** ile sınırlı.
  (Kırmızı VE Beyaz için aynı %2 sınırı geçerlidir.)
- LONG = tam simetrik (yukarı/aşağı ters döner).

---

## 🔴 Kırmızı Ekosistemi

### Kırmızı (ana / parent)
- **Açılış**: 15dk mum kapanışında Donchian50 yön değişimi → flag → fiyat
  Donchian'a değince giriş çizgisi kaydı → statik çizgi cross + EMA800 → açılır.
- **Chandelier**: mesafe = LZ, açılışta devreye girer (trailing stop).
- **Çıkışlar** (üçü de ekosistemin TAMAMINI kapatır):
  1. WINRATE cross → KÂR
  2. Chandelier ters cross → trailing stop
  3. LOSE üstüne çıkış → stop loss
- **Reentry YOK**.

### Mavi (Kırmızı'nın hedge'i)
- **Açılış**: fiyat Kırmızı tablosunun **LS1 bölgesine** girince (konum bazlı).
- **Yön**: Kırmızı'nın tersi.
- **Çıkış**:
  1. Kırmızı kapanırsa (mutlak bağlılık) → kapanır
  2. Kırmızı LOSE üstüne çıkış → kapanır
  3. Kırmızı LS TAMPON altına geçiş → KÂR ile kapanır (Kırmızı açık kalır)
- **Reentry VAR**: Kırmızı yaşadıkça tekrar açılabilir.

### Sarı 1 ve Sarı 2 (trend pekiştirici primary'ler)
- **Sarı 1**: fiyat Kırmızı **ST1 bölgesine** girince doğar. Kendi tablosu
  (çapa = Kırmızı ST1), LOSE = Kırmızı giriş, WINRATE = Kırmızı WINRATE.
- **Sarı 2**: fiyat Kırmızı **ST2 bölgesine** girince doğar. Çapa = Kırmızı ST2,
  LOSE = Kırmızı ST1, WINRATE = Kırmızı WINRATE.
- Her biri kendi chandelier'ini (mesafe = LZ) ve kendi hedge'ini (Mavi 1 / Mavi 2)
  taşır.
- **Çıkışlar**: WINRATE / Chandelier / LOSE → Sarı + kendi Mavi'si kapanır
  (Kırmızı'ya dokunmaz).
- **Reentry YOK**.

### Mavi 1 / Mavi 2
- Sarı 1 / Sarı 2'nin hedge'leri. Mavi ile aynı mantık (LS1'de açılır,
  reentry var, parent'a mutlak bağlı).

---

## ⚪️ Beyaz Ekosistemi

Kırmızı ekosistemiyle **birebir aynı** yapı. İsimler:
`Kırmızı→Beyaz`, `Sarı→Turuncu`, `Mavi→Mor`.

**Tek fark — Beyaz'ın açılışı**: 15dk mum kapanışı BEKLEMEZ. Fiyat Donchian50
üst/alt çizgisine **anlık değince** flag + giriş çizgisi kaydedilir
(giriş çizgisi = Donchian'ın 1/4 içinde). Sonra giriş çizgisi cross + EMA800 →
Beyaz açılır. Tablo kurulduktan sonraki HER ŞEY Kırmızı ile aynıdır.

---

## 🔗 Bağlılık Zinciri

```
KIRMIZI (parent)
   ├── MAVİ            (Kırmızı LS1)
   ├── SARI 1          (Kırmızı ST1)  ──► MAVİ 1 (Sarı 1 LS1)
   └── SARI 2          (Kırmızı ST2)  ──► MAVİ 2 (Sarı 2 LS1)
```

- Kırmızı kapanırsa → zincirin TAMAMI kapanır.
- Sarı 1 kapanırsa → sadece Mavi 1 kapanır.
- Sarı 2 kapanırsa → sadece Mavi 2 kapanır.

---

## 🎰 Slot Kuralları

- Her coine en fazla 1 Kırmızı ekosistemi + 1 Beyaz ekosistemi.
- Her ekosistemde en fazla 6 işlem.
- Aynı coin+yön için Bybit'te tek hard SL tutulur → çakışmada **en geniş** SL
  kullanılır (SHORT'ta en yüksek, LONG'da en düşük). Her işlem kendi LOSE
  çizgisinde yazılımsal kapanır.

---

## 💬 Telegram Komutları

| Komut | Açıklama |
|---|---|
| `/start` | Trading başlat |
| `/stop` | Trading durdur |
| `/status` | Anlık durum |
| `/report` | Saatlik raporu zorla |
| `/pause SEMBOL` | Coin için yeni işlem açılmasını duraklat |
| `/resume SEMBOL` | Devam ettir |
| `/help` | Komut listesi |

İşlem açılış/kapanış, seviye değişimi, hata ve uyarılar anlık bildirilir.
Saatlik / 12 saatlik / 24 saatlik raporlar otomatik gönderilir.

---

## ⚙️ Parametreler (config.json)

| Parametre | Değer | Açıklama |
|---|---|---|
| `leverage` | 50 | Kaldıraç |
| `stake_pct` | 2.0 | Her işlem bakiyenin %2'si |
| `hard_sl_pct` | 2.0 | Borsa SL %2 (çakışmada en geniş) |
| `max_lose_pct` | 2.0 | LOSE ZONE max genişliği (Kırmızı VE Beyaz) |
| `donchian_period` | 50 | Donchian periyodu |
| `ema_period` | 800 | EMA periyodu |
| `timeframe` | 15 | 15 dakikalık mumlar |
| `candle_count` | 900 | Başlangıçta çekilen mum sayısı |
| `thread_scan_interval_sec` | 1 | Thread tarama aralığı |

> Not: `risk_reward` artık kullanılmıyor (WINRATE = E − 5·LZ formülüyle
> hesaplanıyor); config'de zararsız olarak duruyor.

---

## 🗂 Dosya Yapısı

| Dosya | Görev |
|---|---|
| `main.py` | Giriş noktası, scheduler, thread wiring |
| `geometry.py` | Ortak tablo geometrisi (LOSE ZONE / LS / ST / WINRATE) |
| `trade_manager.py` | Ekosistem slot yönetimi, açma/kapatma, PnL, hard SL |
| `red_thread.py` / `white_thread.py` | Ana thread'ler + Sarı/Turuncu doğurma |
| `yellow_thread.py` / `orange_thread.py` | Sarı 1/2, Turuncu 1/2 primary'leri |
| `blue_thread.py` / `purple_thread.py` | Mavi/Mor hedge izleyiciler |
| `data_manager.py` | Bybit veri + emir katmanı |
| `telegram_thread.py` | Bildirimler, komutlar, raporlar |
| `config_loader.py` / `config.json` | Ayarlar |
| `utils.py` / `indicators.py` | Yardımcılar, indikatörler |
