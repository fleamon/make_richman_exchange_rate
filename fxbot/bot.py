"""텔레그램 알림 문구와 명령 처리.

환율은 토스 앱과 같은 단위로 보여준다 (엔·루피아·동은 100 단위).
이 봇은 알려주기만 한다 — 매수·보유 기록은 하지 않는다.
"""

import html
from datetime import datetime, timedelta

from . import odds
from .config import Config, Currency
from .rates import Quote
from .strategy import PRIORITY, percentile

HELP = """환율 알림 봇
환율 — 전체 통화 현재 환율과 기간 내 위치
신호 — 매수 확률 표 지금 보기
(/rates /signal 도 가능)"""

ALIASES = {
    "환율": "rates", "rates": "rates",
    "신호": "signal", "signal": "signal",
    "도움말": "help", "help": "help", "start": "help",
}


def fx(v: float, cur: Currency) -> str:
    """1단위당 원화 → 토스 표시 단위 문자열."""
    q = v * cur.unit
    return f"{q:,.2f}" if q >= 1 else f"{q:,.4f}"


def label(cur: Currency) -> str:
    return f"{cur.code}({cur.name})"


def handle(text: str, cfg: Config, quotes: dict[str, Quote], now: datetime) -> str:
    parts = text.strip().lstrip("/").split()
    cmd = ALIASES.get(parts[0].split("@")[0].lower()) if parts else None
    if cmd == "rates":
        return rates_text(cfg, quotes)
    if cmd == "signal":
        return report_text(now, cfg, quotes)
    return HELP


def rates_text(cfg: Config, quotes: dict[str, Quote]) -> str:
    lines = [f"현재 환율 (최근 {cfg.strategy.lookback_days}일 중 위치, 0%=최저)"]
    for code, cur in cfg.currencies.items():
        q = quotes.get(code)
        if q:
            lines.append(f"{label(cur)} {fx(q.price, cur)}  {percentile(q.price, q.history):.0f}%  "
                         f"[{fx(min(q.history), cur)} ~ {fx(max(q.history), cur)}]")
        else:
            lines.append(f"{label(cur)} 조회 실패")
    return "\n".join(lines)


SHOW_HORIZONS = (1, 3, 5, 10)   # 표에 보이는 확률 열 (20거래일은 폭이 모자라 뺐다)
HEADER_ICON = "🏳️"   # 머리글 줄 앞 자리 채움 — 국기와 같은 폭


def flag(code: str) -> str:
    """통화 코드 앞 두 글자(= 국가 코드)로 국기 이모지. EUR 은 EU 깃발."""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code[:2])


def _buy_rows(cfg: Config, quotes: dict[str, Quote], low_shift: float = 0) -> list[tuple[str, str]]:
    """전 통화 (국기, 고정폭 ASCII 행) 목록 (머리글 포함). 하위 % 오름차순, 같은 %는 단기 확률 높은 순.

    행은 영문·숫자만 쓴다 — 줄마다 인라인 코드(고정폭)로 감싸 열을 맞추고, 국기는 코드 밖 앞에 둔다.
    low_shift: 표 모양 확인용 — 하위 % 를 그만큼 올려서 만든다 (정기 알림은 0).
    """
    table = odds.load()
    rank = {c: i for i, c in enumerate(PRIORITY)}
    rows = []
    for q in quotes.values():
        pct = min(percentile(q.price, q.history) + low_shift, 100)
        rows.append((odds.probabilities(table, q.code, pct), pct, q))
    rows.sort(key=lambda r: (round(r[1]), -(r[0].get(odds.RANK_HORIZON) or 0), rank.get(r[2].code, len(rank))))

    # 표에서는 천 단위 쉼표를 뺀다 — 환율 열이 한 칸 줄어 통화 코드와 환율 사이 빈칸이 준다
    prices = {q.code: fx(q.price, cfg.currencies[q.code]).replace(",", "") for _, _, q in rows}
    lows = {q.code: f"{pct:.0f}%" for _, pct, q in rows}
    # 환율·L% 열은 가장 긴 값에 딱 맞춘다 (남는 빈칸 없이, 짧은 값만 앞에 공백을 채워 줄을 맞춘다)
    w = max([len("RATE"), *map(len, prices.values())])
    lw = max([len("L%"), *map(len, lows.values())])
    ups = {q.code: [f"{probs[h] * 100:.0f}" if h in probs else "-" for h in SHOW_HORIZONS] for probs, _, q in rows}
    # 확률 열은 한 칸씩 띄우고 오른쪽 정렬. 열 폭은 머리글·값 중 긴 쪽 — '10d' '20d' 열만 한 자 넓어 앞이 두 칸이 된다.
    labels = [str(h) for h in SHOW_HORIZONS]   # 머리글은 거래일 수만 (1 3 5 10)
    pws = [max([len(lab), *(len(us[i]) for us in ups.values())]) for i, lab in enumerate(labels)]
    fmt = lambda ccy, rate, low, us: (f"{ccy:<3} {rate:>{w}} {low:>{lw}} "
                                      + " ".join(f"{u:>{pw}}" for u, pw in zip(us, pws)))
    body = [(HEADER_ICON, fmt("CCY", "RATE", "L%", labels))]
    body += [(flag(q.code), fmt(q.code, prices[q.code], lows[q.code], ups[q.code])) for _, _, q in rows]
    return body


def report_text(now: datetime, cfg: Config, quotes: dict[str, Quote], low_shift: float = 0,
                test: bool = False) -> str:
    """정기 알림: 날짜·시간과 전 통화 매수 확률 표.

    코드 블록(<pre>) 대신 줄마다 인라인 <code> 로 감싸 HTML 로 보낸다 — 열은 고정폭으로 맞고,
    국기는 코드 밖에 있어 컬러로 보인다. test 면 제목에 [테스트] 를 붙인다.
    """
    kst = now + timedelta(hours=9)
    days = cfg.strategy.lookback_days
    note = [f"※ L% = 최근 {days}일 중 위치 (0%=최저)",
            f"※ {SHOW_HORIZONS[0]}~{SHOW_HORIZONS[-1]} = 그 거래일 뒤 오른 비율(%)",
            "※ 과거 10년 같은 구간 기준 · 참고용"]
    table = "\n".join(f"{icon} <code>{html.escape(row)}</code>" for icon, row in _buy_rows(cfg, quotes, low_shift))
    test = f"[테스트 · 하위% +{low_shift:.0f}] " if test else ""
    return f"{test}📍 {kst:%m/%d %H:%M} 환율 (하위% 낮은 순)\n\n{table}\n\n" + "\n".join(note)
