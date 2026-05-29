"""Telegram bildirim modülü — sade HTTP POST.

Telegram'ın senkron HTTP API'si yeterli. Bot kritik anlarda mesaj gönderir;
gönderim başarısız olursa sadece log'a yazar, akışı durdurmaz.
"""

from __future__ import annotations

import logging
import requests
from typing import Optional

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: int = 10) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._enabled = bool(token) and bool(chat_id)
        if not self._enabled:
            log.warning("Telegram devre dışı (token/chat_id eksik)")

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self._enabled:
            log.info("[TG-DISABLED] %s", text)
            return False
        try:
            r = requests.post(
                self.api_url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout,
            )
            if r.status_code != 200:
                log.error("Telegram %d: %s", r.status_code, r.text[:200])
                return False
            return True
        except Exception as e:
            log.error("Telegram gönderim hatası: %s", e)
            return False
