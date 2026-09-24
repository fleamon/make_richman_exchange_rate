"""python -m fxbot [run|keygen|chatid|rates]

run    : (GitHub Actions) 텔레그램 기록 명령 처리 → 환율 조회 → 신호 알림 → 암호화 상태 저장
keygen : STATE_KEY 로 쓸 암호화 키 생성
chatid : TELEGRAM_TOKEN 으로 봇에 온 메시지의 chat id 출력 (최초 설정용)
rates  : 현재 환율과 3개월 위치를 화면에 출력 (로컬 확인용)

공개 저장소의 Actions 로그는 누구나 볼 수 있으므로, run 은 보유·거래 정보를 로그에 출력하지 않는다.
"""

import sys
from datetime import datetime, timedelta, timezone

from . import config, rates, state, strategy
from .bot import handle, rates_text, signal_text
from .ledger import replay
from .telegram import Telegram


def run() -> None:
    cfg = config.load()
    st = state.load()
    before = state.dumps(st)
    now = datetime.now(timezone.utc)
    tg = Telegram.from_env()

    quotes, errors = rates.fetch_all(cfg.currencies, cfg.strategy.lookback_days)
    print(f"환율 조회 {len(quotes)}/{len(cfg.currencies)}")
    for e in errors:
        print(f"  실패: {e}")

    if tg is None:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 가 없어 알림·명령 처리를 건너뜁니다.")
    else:
        texts, st["tg_offset"] = tg.updates(st["tg_offset"])
        for text in texts:
            tg.send(handle(text, st, cfg, quotes, now))

        signals = strategy.run(cfg, quotes, replay(st["trades"], cfg.currencies), st["alerts"], now)
        for sig in signals:
            tg.send(signal_text(sig, cfg.currencies[sig.code], cfg))

        last_err = st["alerts"].get("error:fetch")
        if not errors:
            st["alerts"].pop("error:fetch", None)
        elif not last_err or now - datetime.fromisoformat(last_err["ts"]) >= timedelta(hours=cfg.strategy.realert_hours):
            st["alerts"]["error:fetch"] = {"ts": now.isoformat(), "price": 0}
            tg.send("⚠️ 환율 조회 실패\n" + "\n".join(errors))
        print(f"명령 {len(texts)}건 처리, 알림 {len(signals)}건 발송")

    if state.dumps(st) != before:
        state.save(st)
        print("상태 변경 → state.enc 갱신")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "keygen":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
    elif cmd == "chatid":
        import os
        import requests
        r = requests.get(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/getUpdates", timeout=20).json()
        ids = {(u["message"]["chat"]["id"], u["message"]["chat"].get("first_name", "")) for u in r["result"] if "message" in u}
        print("\n".join(f"{i}  {n}" for i, n in ids) or "봇에게 아무 메시지나 먼저 보낸 뒤 다시 실행하세요.")
    elif cmd == "rates":
        cfg = config.load()
        quotes, errors = rates.fetch_all(cfg.currencies, cfg.strategy.lookback_days)
        print(rates_text(cfg, quotes))
        for e in errors:
            print("실패:", e)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
