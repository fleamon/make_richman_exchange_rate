"""매수·매도 기록과 알림 이력을 암호화해 저장소에 보관한다.

공개 저장소라 평문으로 두면 안 된다. 키는 GitHub Secret(STATE_KEY)에만 둔다.
"""

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import ROOT

STATE_PATH = ROOT / "state" / "state.enc"
PAD = 4096  # 암호문 길이로 거래 건수를 추정하지 못하게 평문을 이 단위로 채운다


def empty() -> dict:
    return {"version": 1, "tg_offset": 0, "trades": [], "alerts": {}}


def _fernet() -> Fernet:
    key = os.environ.get("STATE_KEY")
    if not key:
        raise SystemExit("STATE_KEY 환경변수가 없습니다. (python -m fxbot keygen 으로 생성)")
    return Fernet(key.encode())


def dumps(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True)


def load(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return empty()
    try:
        return {**empty(), **json.loads(_fernet().decrypt(path.read_bytes()))}
    except InvalidToken:
        raise SystemExit("state.enc 복호화 실패: STATE_KEY 가 저장 당시 키와 다릅니다.")


def save(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = dumps(state).encode()
    raw += b" " * (-len(raw) % PAD)
    path.write_bytes(_fernet().encrypt(raw))
