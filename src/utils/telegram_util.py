from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests


class TelegramUtil:
    def __init__(
        self,
        *,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.timeout_seconds = timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_photo(self, photo_path: str | Path, caption: str = "") -> dict[str, Any]:
        if not self.is_configured:
            raise EnvironmentError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be configured.")

        path = Path(photo_path)
        if not path.exists():
            raise FileNotFoundError(f"Telegram photo path does not exist: {path}")

        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        with path.open("rb") as photo:
            response = requests.post(
                url,
                data={
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={"photo": (path.name, photo, "image/png")},
                timeout=self.timeout_seconds,
            )

        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram sendPhoto failed: {payload}")
        return payload
