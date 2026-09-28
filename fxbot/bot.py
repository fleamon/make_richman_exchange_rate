"""텔레그램 알림 문구와 명령 처리.

환율은 토스 앱과 같은 단위로 보여준다 (엔·루피아·동은 100 단위).
이 봇은 알려주기만 한다 — 매수·보유 기록은 하지 않는다.
"""

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


HEADER_ICON = "🏳️"   # 머리글 줄 앞 자리 채움 — 국기와 같은 폭
FS = "\u2007"         # figure space — 숫자 한 자와 폭이 같은 공백


def flag(code: str) -> str:
    """통화 코드 앞 두 글자(= 국가 코드)로 국기 이모지. EUR 은 EU 깃발."""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code[:2])


def _num(s: str, width: int) -> str:
    """숫자 폭 공백으로 오른쪽 정렬."""
    return FS * (width - len(s)) + s


def _buy_rows(cfg: Config, quotes: dict[str, Quote]) -> list[str]:
    """전 통화 행들 (머리글 포함). 하위 % 오름차순, 같은 %는 단기 확률 높은 순.

    코드 블록 없이 가변폭 글꼴에서도 열이 맞게: 국기(폭이 모두 같다) → 숫자 열 → 통화 코드 순으로 두고,
    숫자 앞 빈칸은 숫자 폭 공백으로 채운다. 글자 폭이 제각각인 통화 코드는 줄 맨 끝이라 열을 밀지 않는다.
    환율은 쉼표 없이 쓴다 (쉼표는 숫자보다 좁다).
    """
    table = odds.load()
    rank = {c: i for i, c in enumerate(PRIORITY)}
    rows = []
    for q in quotes.values():
        pct = percentile(q.price, q.history)
        rows.append((odds.probabilities(table, q.code, pct), pct, q))
    rows.sort(key=lambda r: (round(r[1]), -(r[0].get(odds.RANK_HORIZON) or 0), rank.get(r[2].code, len(rank))))

    prices = {q.code: f"{q.price * cfg.currencies[q.code].unit:.2f}" for _, _, q in rows}
    w = max([7, *map(len, prices.values())])   # 환율이 7자를 넘으면 열을 넓힌다

    def line(icon: str, rate: str, low: str, ups: list[str], ccy: str) -> str:
        cols = [_num(rate, w), _num(low, 4), *[_num(u, 3) for u in ups]]
        return f"{icon} " + "  ".join(cols) + f"  {ccy}"

    body = [line(HEADER_ICON, "RATE", "LOW%", [f"{h}d" for h in odds.HORIZONS], "CCY")]
    for probs, pct, q in rows:
        ups = [f"{probs[h] * 100:.0f}" if h in probs else "-" for h in odds.HORIZONS]
        body.append(line(flag(q.code), prices[q.code], f"{pct:.0f}%", ups, q.code))
    return body


def report_text(now: datetime, cfg: Config, quotes: dict[str, Quote]) -> str:
    """정기 알림: 날짜·시간과 전 통화 매수 확률 표 (코드 블록·인라인 코드 없이 일반 텍스트)."""
    kst = now + timedelta(hours=9)
    days = cfg.strategy.lookback_days
    note = [f"※ LOW% = 최근 {days}일 중 위치 (0%=최저)",
            "※ 1d~20d = 그 거래일 뒤 오른 비율(%)",
            "※ 과거 10년 같은 구간 기준 · 참고용"]
    table = "\n".join(_buy_rows(cfg, quotes))
    return f"📍 {kst:%m/%d %H:%M} 환율 (하위% 낮은 순)\n\n{table}\n\n" + "\n".join(note)
