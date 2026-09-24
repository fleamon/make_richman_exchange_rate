"""텔레그램 봇: 신호 알림을 보내고, 사용자가 보낸 매수·매도 기록 명령을 받아온다.

웹훅 없이 getUpdates 로 가져오므로 서버가 필요 없다 (텔레그램이 미확인 메시지를 24시간 보관).
"""

import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)

    @classmethod
    def from_env(cls) -> "Telegram | None":
        token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        return cls(token, chat_id) if token and chat_id else None

    def _call(self, method: str, **params) -> dict:
        # 요청 URL 에 토큰이 들어가므로, 오류 메시지에 URL 이 섞여 공개 로그에 찍히지 않게 한다
        try:
            r = requests.post(API.format(token=self.token, method=method), json=params, timeout=20)
        except requests.RequestException as e:
            raise TelegramError(f"{method}: {type(e).__name__}") from None
        if not r.ok:
            raise TelegramError(f"{method}: HTTP {r.status_code}")
        return r.json()["result"]

    def send(self, text: str) -> None:
        for i in range(0, len(text), 4000):
            self._call("sendMessage", chat_id=self.chat_id, text=text[i : i + 4000])

    def updates(self, offset: int) -> tuple[list[str], int]:
        """내 채팅에서 온 새 메시지 텍스트 목록과 다음 offset."""
        texts = []
        for u in self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"]):
            offset = max(offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) == self.chat_id and msg.get("text"):
                texts.append(msg["text"])
        return texts, offset
