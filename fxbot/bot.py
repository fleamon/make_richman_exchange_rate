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

HELP = """환율 알림 봇 (매수·보유 기록 기능은 없앴습니다)
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


def _buy_rows(cfg: Config, quotes: dict[str, Quote]) -> list[str]:
    """전 통화 행들 (머리글·구분선 포함). 하위 % 오름차순, 같은 %는 단기 확률 높은 순.

    코드 블록(고정폭)에 넣으므로 영문·숫자만 쓴다 — 국기·한글은 폭이 일정하지 않아 열이 밀린다.
    """
    table = odds.load()
    rank = {c: i for i, c in enumerate(PRIORITY)}
    rows = []
    for q in quotes.values():
        pct = percentile(q.price, q.history)
        rows.append((odds.probabilities(table, q.code, pct), pct, q))
    rows.sort(key=lambda r: (round(r[1]), -(r[0].get(odds.RANK_HORIZON) or 0), rank.get(r[2].code, len(rank))))

    prices = {q.code: fx(q.price, cfg.currencies[q.code]) for _, _, q in rows}
    w = max([8, *map(len, prices.values())])   # 환율이 8자를 넘으면 열을 넓혀 줄을 맞춘다
    header = f"{'CCY':<3} {'RATE':>{w}} {'LOW%':>4} " + " ".join(f"{f'{h}d':>3}" for h in odds.HORIZONS)
    body = [header, "-" * len(header)]
    for probs, pct, q in rows:
        ups = " ".join(f"{probs[h] * 100:>3.0f}" if h in probs else "  -" for h in odds.HORIZONS)
        body.append(f"{q.code:<3} {prices[q.code]:>{w}} {pct:>3.0f}% {ups}")
    return body


def report_text(now: datetime, cfg: Config, quotes: dict[str, Quote]) -> str:
    """정기 알림: 날짜·시간과 전 통화 매수 확률 표 (표는 코드 블록 <pre> 로 열을 맞춘다)."""
    kst = now + timedelta(hours=9)
    days = cfg.strategy.lookback_days
    note = [f"※ LOW% = 최근 {days}일 중 위치 (0%=최저)",
            "※ 1d~20d = 그 거래일 뒤 오른 비율(%)",
            "※ 과거 10년 같은 구간 기준 · 참고용"]
    table = html.escape("\n".join(_buy_rows(cfg, quotes)))
    return f"📍 {kst:%m/%d %H:%M} 환율 (하위% 낮은 순)\n<pre>{table}</pre>\n" + "\n".join(note)
