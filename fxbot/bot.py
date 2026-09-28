"""텔레그램 알림 문구와 명령 처리.

환율은 토스 앱과 같은 단위로 보여준다 (엔·루피아·동은 100 단위).
이 봇은 알려주기만 한다 — 매수·보유 기록은 하지 않는다.
"""

import html
import unicodedata
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


def flag(code: str) -> str:
    """통화 코드 앞 두 글자(= 국가 코드)로 국기 이모지. EUR 은 EU 깃발."""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code[:2])


def _width(s: str) -> int:
    """고정폭 글꼴에서 차지하는 칸 수 (한글·이모지는 두 칸, 국기는 두 글자가 합쳐져 두 칸)."""
    return sum(1 if 0x1F1E6 <= ord(c) <= 0x1F1FF else
               2 if ord(c) > 0x2FFF or unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int, right: bool = True) -> str:
    fill = " " * max(width - _width(s), 0)
    return fill + s if right else s + fill


PROB_W = (3, 3, 3, 4, 4)   # 확률 열 폭 — '10d' '20d' 머리글이 붙어 보이지 않게 뒤 두 열만 한 칸 넓다


def _table(rows: list[str]) -> str:
    """고정폭 표. 줄 끝 공백은 텔레그램이 버리므로 각 행은 숫자로 끝나게 만들어 둔다."""
    body = "\n".join(r.rstrip() for r in rows)
    return f"<pre>{html.escape(body, quote=False)}</pre>"


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
    """전 통화 행들 (머리글 포함). 하위 % 오름차순, 같은 %는 단기 확률 높은 순.

    표 안에는 한글을 넣지 않는다 — 휴대폰 고정폭 글꼴에서 한글 폭이 일정하지 않아 열이 밀린다.
    통화 7 + 환율 9 + 하위 4 + 확률 17 = 37칸.
    """
    table = odds.load()
    rank = {c: i for i, c in enumerate(PRIORITY)}
    rows = []
    for q in quotes.values():
        pct = percentile(q.price, q.history)
        rows.append((odds.probabilities(table, q.code, pct), pct, q))
    rows.sort(key=lambda r: (round(r[1]), -(r[0].get(odds.RANK_HORIZON) or 0), rank.get(r[2].code, len(rank))))

    body = [" " * 20 + "".join(_pad(f"{h}d", w) for h, w in zip(odds.HORIZONS, PROB_W))]
    for probs, pct, q in rows:
        cells = [_pad(f"{flag(q.code)} {q.code}", 7, right=False),
                 _pad(fx(q.price, cfg.currencies[q.code]), 9), _pad(f"{pct:.0f}%", 4)]
        cells += [_pad(f"{probs[h] * 100:.0f}" if h in probs else "-", w)
                  for h, w in zip(odds.HORIZONS, PROB_W)]
        body.append("".join(cells))
    return body


def report_text(now: datetime, cfg: Config, quotes: dict[str, Quote]) -> str:
    """정기 알림: 날짜·시간과 전 통화 매수 확률 표 하나.

    코드 블록을 하나만 쓴다 — 텔레그램이 블록마다 글꼴을 따로 줄여서, 나눠 보내면
    표끼리 글자 크기와 줄 간격이 달라진다.
    """
    kst = now + timedelta(hours=9)
    days = cfg.strategy.lookback_days
    note = [f"※ 하위 % = 최근 {days}일 중 위치 (0%=최저)",
            "※ 1d~20d = 그 거래일 뒤 오른 비율(%)",
            "※ 과거 10년 같은 구간 기준 · 참고용"]
    return (f"📍 {kst:%m/%d %H:%M} 환율 (하위 % 낮은 순)\n\n"
            f"{_table(_buy_rows(cfg, quotes))}\n\n"
            + html.escape("\n".join(note), quote=False))
