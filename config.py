"""
config.py - config.json okur, dogrular ve tiplendirir.

Bybit API key / secret ve Telegram token / chat_id
ortam degiskenlerinden (environment variables) alinir:
  BYBIT_API_KEY
  BYBIT_API_SECRET
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import json
import os
from typing import List

# Bybit interval string eslesmesi (dakika -> API kodu)
_TIMEFRAME_MAP = {
    "1":    "1",
    "3":    "3",
    "5":    "5",
    "15":   "15",
    "30":   "30",
    "60":   "60",
    "120":  "120",
    "240":  "240",
    "360":  "360",
    "720":  "720",
    "1440": "D",
}


class Config:
    def __init__(self, path: str = "config.json"):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # --- Ortam degiskenleri ---
        self.bybit_api_key    = os.environ.get("BYBIT_API_KEY", "")
        self.bybit_api_secret = os.environ.get("BYBIT_API_SECRET", "")
        self.telegram_token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        if not self.bybit_api_key or not self.bybit_api_secret:
            raise ValueError("BYBIT_API_KEY ve BYBIT_API_SECRET ortam degiskenleri tanimlanmali.")
        if not self.telegram_token or not self.telegram_chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID ortam degiskenleri tanimlanmali.")

        # --- Genel ---
        g = raw["genel"]
        self.stake_percent:   float = float(g["STAKE_PERCENT"])
        self.max_open_trades: int   = int(g["MAX_OPEN_TRADES"])
        self.leverage:        int   = int(g["LEVERAGE"])
        self.sl_percent:      float = float(g["SL_PERCENT"])

        # --- Bant ---
        b = raw["bant"]
        self.ema_period:        int   = int(b["EMA_PERIOD"])
        self.atr_period:        int   = int(b["ATR_PERIOD"])
        self.band_multiplier:   float = float(b["BAND_MULTIPLIER"])
        self.buffer_multiplier: float = float(b["BUFFER_MULTIPLIER"])

        # --- Tarama ---
        t = raw["tarama"]
        self.timeframe:       str   = str(t["TIMEFRAME"])
        self.scan_seconds:    int   = int(t["SCAN_SECONDS"])
        self.history_seconds: int   = int(t["HISTORY_SECONDS"])

        if self.timeframe not in _TIMEFRAME_MAP:
            raise ValueError(
                f"Gecersiz TIMEFRAME: {self.timeframe}. "
                f"Gecerli degerler: {list(_TIMEFRAME_MAP.keys())}"
            )

        # --- Cikis ---
        c = raw["cikis"]
        self.ce1_atr:      float = float(c["CE1_ATR"])
        self.ce1_trail:    float = float(c["CE1_TRAIL"])
        self.ce2_atr:      float = float(c["CE2_ATR"])
        self.ce2_trail:    float = float(c["CE2_TRAIL"])
        self.winrate_atr:  float = float(c["WINRATE_ATR"])
        self.winrate_trail: float = float(c["WINRATE_TRAIL"])

        # --- Emir ---
        e = raw["emir"]
        self.entry_attempts:   int = int(e["ENTRY_ATTEMPTS"])
        self.exit_attempts:    int = int(e["EXIT_ATTEMPTS"])
        self.attempt_wait_sec: int = int(e["ATTEMPT_WAIT_SEC"])

        # --- Rapor ---
        r = raw["rapor"]
        self.report_15min:       bool      = bool(r["REPORT_15MIN"])
        self.report_hourly:      bool      = bool(r["REPORT_HOURLY"])
        self.report_8h_hours:    List[int] = [int(h) for h in r["REPORT_8H_HOURS"]]
        self.report_daily_hour:  int       = int(r["REPORT_DAILY_HOUR"])

        # --- Coinler ---
        self.coins: List[str] = [str(s) for s in raw["coinler"]]
        if not self.coins:
            raise ValueError("Coin listesi bos olamaz.")

    def bybit_interval(self) -> str:
        """Bybit API icin interval string."""
        return _TIMEFRAME_MAP[self.timeframe]
