# Bybit Futures Bot

Donchian Channel (50) + EMA800 trend filtreli, çok seviyeli trailing stop'lu Bybit Futures botu.

## Kurulum

```bash
pip install pandas numpy requests pybit pytest
```

## Çalıştırma

Aşağıdaki environment değişkenleri **zorunlu**:

```bash
export BYBIT_API_KEY="..."
export BYBIT_API_SECRET="..."
```

Telegram bildirimi için **opsiyonel**:

```bash
export TELEGRAM_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
```

Ardından:

```bash
python main.py
```

İlk çalıştırmada bot otomatik olarak:
- Tüm `coins` listesi için **Hedge Mode** açar (long+short aynı anda).
- 50x kaldıraç ayarlar (`config.json`'dan değiştirilebilir).
- Bakiyenin %5'ini stake olarak alır.
- 1000 mum × 15dk veri çeker ve EMA800/Donchian50 hesaplar.

## Test

```bash
python -m pytest tests/ -v
```

75 test. Hepsi geçmeli.

## Yapılandırma — `config.json`

| Alan | Açıklama |
|------|----------|
| `exchange.leverage` | Kaldıraç (varsayılan 50) |
| `exchange.testnet` | true = Bybit testnet, false = canlı |
| `indicators.ema_period` | EMA periyodu (800) |
| `indicators.donchian_period` | Donchian periyodu (50) |
| `timeframes.candle_interval` | "15" (15 dakika) |
| `timeframes.price_scan_seconds` | Fiyat tarama frekansı (5sn) |
| `timeframes.balance_update_hours` | Bakiye yenileme (8sa) |
| `risk.stake_percent` | Bakiyenin %5'i |
| `risk.sl_percent` | %2 borsa SL |
| `risk.risk_reward_ratio` | 3 (WINRATE = entry + 3d) |
| `limits.max_positions` | 20 coin |
| `limits.max_trades_per_side` | Aynı coin/yön: 3 işlem |
| `coins` | İzlenen sembol listesi |

## Mantık özeti

### Flag oluşumu (15dk kapanışta)
- **Long flag**: Donchian üst düştü + close > EMA800
- **Short flag**: Donchian alt yükseldi + close < EMA800
- Flag silinme: işlem açılınca / fiyat EMA800'e değince
- Açık işlemken yeni flag koşulu varsa eski flag'in üzerine yazılır

### Giriş
- Fiyat flag'in tetik çizgisine (long: üst, short: alt) dokundu mu? → **market emir** + borsa SL (%2)
- Aynı coin/yön: max 3 işlem; toplam 20 coin slot

### Seviye matematiği (Long; short ters)
```
d = entry - LOSE_EXIT
LOSE_EXIT = donchian_alt  (eğer entry'den %2'den uzaksa → entry * 0.98)
WINRATE = entry + 3*d
[entry, WINRATE] aralığı 6 eşit zona bölünür → step = d/2

Zone (level)    Aralık
1 ENTRY         [entry,      entry+d/2)
2 ST1           [entry+d/2,  entry+d)
3 ST2           [entry+d,    entry+3d/2)
4 ST3           [entry+3d/2, entry+2d)
5 ST4           [entry+2d,   entry+5d/2)
6 ST5           [entry+5d/2, entry+3d)
```

### Çıkış (Long)

| Mevcut Level | Geriye dönüş çizgisi | + her durumda |
|--------------|----------------------|---------------|
| 1 ENTRY      | LOSE_EXIT            | EMA800 |
| 2 ST1        | LOSE_EXIT            | EMA800 |
| 3 ST2        | ENTRY                | EMA800 |
| 4 ST3        | ST1 line             | EMA800 |
| 5 ST4        | ST2 line             | EMA800 |
| 6 ST5        | ST3 line VEYA WINRATE üstü | EMA800 |

Seviye yalnızca **yukarı** sayılır, geri düşmez.

### Raporlar
- **Saatlik**: açık işlemler, anlık PnL
- **Z (8 saatlik)**: kapanan işlemler, win rate, çıkış sebepleri
- **X (24 saatlik)**: tam istatistik, hacim, coin bazlı performans

### State / Restart
Her trade open/close anında `state.json` güncellenir.
Yeniden başlatıldığında:
- state'teki işlemler yüklenir
- Bybit'ten gerçek pozisyonlar çekilir, karşılaştırılır
- Bilinmeyen pozisyon varsa Telegram'a uyarı

## Dosya yapısı

```
bot/
├── main.py                    # Orchestrator
├── config.json
├── core/
│   ├── indicators.py          # EMA, Donchian, ATR
│   ├── flag_manager.py        # Flag tespit/saklama
│   ├── trade_manager.py       # Level matematiği + exit (KRİTİK)
│   ├── data_fetcher.py        # Bybit veri çekme
│   ├── order_manager.py       # Bybit emir
│   ├── balance_manager.py     # Periyodik bakiye
│   └── state.py               # state.json
├── reporting/
│   ├── telegram_bot.py
│   ├── notifications.py       # Mesaj formatları
│   └── reports.py             # Periyodik raporlar
└── tests/                     # 75 unit test
```

## Bilinen kısıtlar

1. **Canlı API testi yapılmadı** — kod sandbox'ta yazıldı. Live'a çıkmadan önce Bybit testnet'te dene (`config.json`'da `testnet: true`).
2. **Bybit kline son mum açık olabilir**. Üretim için son satırı `now < timestamp + interval` kontrolüyle drop etmek isteyebilirsin.
3. **Hedge mode** ayarı her sembol için tek seferlik. Eğer hesabın One-Way Mode'da pozisyon varsa "switch mode" hata verir → önce manuel kapatman gerekebilir.
4. **EMA800** Wilder yerine standart EMA (pandas ewm `adjust=False`). Notlarda algoritma belirtilmemişti, klasik EMA tercih edildi.
5. **Reconcile**: `state.json`'da olup borsada olmayan pozisyon için ayrı uyarı eklenebilir (şu an sadece tersi var).
6. **"Arası 5 çizgi, 6 eşit zona"** ifadesi: `[entry, WINRATE]` aralığı 6 eşit zona bölündü (her step = d/2). LOSE_EXIT bağımsız bir alt sınır, zone'a dahil değil. Tabloda Level 1 = ENTRY denmesi bu yorumu destekler. Eğer farklı yorumladıysan `core/trade_manager.py::compute_levels` içindeki step hesabını değiştirebilirsin.

## Deploy

Railway/Render'da çalıştırmak için:
- Procfile: `worker: python main.py`
- Env vars: yukarıdaki 4 değişken
- Disk: `state.json` kalıcı bir volume'e yazılmalı (yoksa restart'ta state kaybolur)
