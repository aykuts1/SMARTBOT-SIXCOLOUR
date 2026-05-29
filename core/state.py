"""State Persistence.

state.json yapısı:
{
  "trades": [
    {
      "trade_id": "...",
      "symbol": "BTCUSDT",
      "side": "long",
      "size": 0.001,
      "opened_at_ts": 1234567890000,
      "level": 3,
      "notional_usdt": 100.0,
      "stake_usdt": 2.0,
      "exchange_sl_order_id": null,
      "levels": {
        "entry": 100.0, "lose_exit": 99.0, "winrate": 103.0,
        "step_lines": [100.5, 101.0, 101.5, 102.0, 102.5, 103.0],
        "side": "long"
      }
    }, ...
  ],
  "flags": { ... FlagManager.dump() ... },
  "stats": {
    "session_open_count": 12,
    "session_close_count": 8,
    "errors_period": 0,
    ...
  }
}
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict
from typing import Any

from core.trade_manager import Trade, TradeLevels

log = logging.getLogger(__name__)


def _trade_to_dict(t: Trade) -> dict:
    return {
        "trade_id": t.trade_id,
        "symbol": t.symbol,
        "side": t.side,
        "size": t.size,
        "level": t.level,
        "opened_at_ts": t.opened_at_ts,
        "notional_usdt": t.notional_usdt,
        "stake_usdt": t.stake_usdt,
        "exchange_sl_order_id": t.exchange_sl_order_id,
        "levels": {
            "side": t.levels.side,
            "entry": t.levels.entry,
            "lose_exit": t.levels.lose_exit,
            "winrate": t.levels.winrate,
            "step_lines": list(t.levels.step_lines),
        },
    }


def _trade_from_dict(d: dict) -> Trade:
    lv = d["levels"]
    levels = TradeLevels(
        side=lv["side"],
        entry=lv["entry"],
        lose_exit=lv["lose_exit"],
        winrate=lv["winrate"],
        step_lines=list(lv["step_lines"]),
    )
    return Trade(
        symbol=d["symbol"],
        side=d["side"],
        size=d["size"],
        levels=levels,
        level=d.get("level", 1),
        opened_at_ts=d.get("opened_at_ts", 0),
        trade_id=d.get("trade_id"),
        exchange_sl_order_id=d.get("exchange_sl_order_id"),
        notional_usdt=d.get("notional_usdt", 0.0),
        stake_usdt=d.get("stake_usdt", 0.0),
    )


def save_state(path: str, trades: list[Trade], flags_dump: dict,
               stats: dict | None = None) -> None:
    """Atomic write: temp dosyaya yaz, rename ile yer değiştir."""
    data = {
        "trades": [_trade_to_dict(t) for t in trades],
        "flags": flags_dump,
        "stats": stats or {},
    }
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_state(path: str) -> tuple[list[Trade], dict, dict]:
    """(trades, flags_dump, stats) döner. Dosya yoksa boş."""
    if not os.path.exists(path):
        return [], {}, {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        trades = [_trade_from_dict(t) for t in data.get("trades", [])]
        flags_dump = data.get("flags", {})
        stats = data.get("stats", {})
        return trades, flags_dump, stats
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.error("state.json bozuk: %s — sıfırdan başlanıyor", e)
        # Bozuk dosyayı yedekle
        backup = f"{path}.broken.{int(__import__('time').time())}"
        try:
            os.rename(path, backup)
            log.error("Bozuk state %s'e taşındı", backup)
        except Exception:
            pass
        return [], {}, {}
