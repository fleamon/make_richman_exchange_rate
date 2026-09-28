"""python -m fxbot [run|keygen|chatid|rates|backtest]

run    : (GitHub Actions) 텔레그램 명령 처리 → 환율 조회 → 매수 확률 알림 → 암호화 상태 저장
keygen : STATE_KEY 로 쓸 암호화 키 생성
chatid : TELEGRAM_TOKEN 으로 봇에 온 메시지의 chat id 출력 (최초 설정용)
backtest: 과거 10년 환율로 '하위 % 구간별 1·3·5·10·20거래일 뒤 상승 확률' 표(fxbot/odds.json) 재생성
rates  : 현재 환율과 3개월 위치를 화면에 출력 (로컬 확인용)

공개 저장소의 Actions 로그는 누구나 볼 수 있으므로, run 은 환율 조회 개수 외에는 로그에 출력하지 않는다.
"""

import os
import sys
from datetime import datetime, timezone

from . import config, odds, rates, state, strategy
from .bot import handle, rates_text, report_text
from .telegram import Telegram


def run() -> None:
    cfg = config.load()
    st = state.load()
    before = state.dumps(st)
    now = datetime.now(timezone.utc)
    tg = Telegram.from_env()

    quotes, _ = rates.fetch_all(cfg.currencies, cfg.strategy.lookback_days)
    print(f"환율 조회 {len(quotes)}/{len(cfg.currencies)}")
    if tg is None:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 가 없어 알림·명령 처리를 건너뜁니다.")
    else:
        texts, st["tg_offset"] = tg.updates(st["tg_offset"])
        for text in texts:
            tg.send(handle(text, cfg, quotes, now))
        # 뒤의 알림 발송이 실패해도 같은 명령을 다음 실행에 또 처리하지 않게 먼저 저장한다
        if state.dumps(st) != before:
            state.save(st)

        slot = strategy.signal_slot(now)
        if st.get("signal_slot") != slot or os.environ.get("FORCE_SIGNAL"):
            tg.send(report_text(now, cfg, quotes))
            st["signal_slot"] = slot
        if os.environ.get("TEST_LOW_SHIFT"):   # 표 모양 확인용 테스트 메시지 (하위 % 를 올려서)
            tg.send(report_text(now, cfg, quotes, low_shift=float(os.environ["TEST_LOW_SHIFT"])))
    st.pop("alerts", None)

    # 공개 로그라 명령·알림 건수나 상태 변경 여부도 남기지 않는다
    if state.dumps(st) != before:
        state.save(st)
    print("완료")


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
    elif cmd == "backtest":
        import json
        import requests
        cfg = config.load()
        with requests.Session() as session:
            hist = {code: sorted(rates.naver_history(session, cur, 44).items()) for code, cur in cfg.currencies.items()}
        table = odds.build(hist, cfg.strategy.lookback_days)
        odds.PATH.write_text(json.dumps(table, ensure_ascii=False, indent=1))
        print(f"{odds.PATH} 갱신 ({table['period'][0]} ~ {table['period'][1]})")
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
