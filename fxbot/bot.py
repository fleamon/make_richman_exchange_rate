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


FIGURE_SPACE, PUNCT_SPACE, FIGURE_DASH = "\u2007", "\u2008", "\u2012"
PUNCT = ",."


def _fit(cell: str, digits: int, puncts: int, fs: str, ps: str) -> str:
    """오른쪽 정렬. 숫자 자리는 fs, 쉼표·점 자리는 ps 로 채운다."""
    p = sum(c in PUNCT for c in cell)
    return fs * (digits - (len(cell) - p)) + ps * (puncts - p) + cell


def _buy_rows(cfg: Config, quotes: dict[str, Quote], plain: bool = False) -> list[str]:
    """전 통화 행들 (머리글·구분선 포함). 하위 % 오름차순, 같은 %는 단기 확률 높은 순.

    영문·숫자만 쓴다 — 국기·한글은 폭이 일정하지 않아 열이 밀린다.
    plain=True 면 코드 블록 없이 가변폭 글꼴에서도 열이 맞게, 빈칸을 숫자 폭 공백(U+2007)과
    쉼표·점 폭 공백(U+2008)으로 채우고 '-' 는 숫자 폭 대시(U+2012)로 쓴다.
    휴대폰 기본 글꼴은 숫자 폭이 모두 같아 숫자 열은 맞고, 통화 코드 글자 폭 차이만 약간 남는다.
    """
    table = odds.load()
    rank = {c: i for i, c in enumerate(PRIORITY)}
    rows = []
    for q in quotes.values():
        pct = percentile(q.price, q.history)
        rows.append((odds.probabilities(table, q.code, pct), pct, q))
    rows.sort(key=lambda r: (round(r[1]), -(r[0].get(odds.RANK_HORIZON) or 0), rank.get(r[2].code, len(rank))))

    fs, ps, dash = (FIGURE_SPACE, PUNCT_SPACE, FIGURE_DASH) if plain else (" ", " ", "-")
    header = ["CCY", "RATE", "LOW%"] + [f"{h}d" for h in odds.HORIZONS]
    cells = [[q.code, fx(q.price, cfg.currencies[q.code]), f"{pct:.0f}%"]
             + [f"{probs[h] * 100:.0f}" if h in probs else dash for h in odds.HORIZONS]
             for probs, pct, q in rows]
    grid = [header] + cells
    # 열마다 숫자 자리·쉼표 자리를 따로 세어, 가장 긴 칸에 맞춰 채운다 (환율이 길어지면 열도 넓어진다)
    puncts = [max(sum(c in PUNCT for c in r[i]) for r in grid) for i in range(len(header))]
    digits = [max(max(len(r[i]) - sum(c in PUNCT for c in r[i]) for r in grid), 3) for i in range(len(header))]
    digits[1] = max(digits[1], 8 - puncts[1])
    lines = [fs.join([r[0] + fs * (digits[0] - len(r[0]))]
                     + [_fit(c, d, p, fs, ps) for c, d, p in zip(r[1:], digits[1:], puncts[1:])]) for r in grid]
    width = sum(digits) + len(header) - 1 + (0 if plain else sum(puncts))
    return [lines[0], dash * width] + lines[1:]


def report_text(now: datetime, cfg: Config, quotes: dict[str, Quote]) -> str:
    """정기 알림: 날짜·시간과 전 통화 매수 확률 표 (코드 블록 없이 일반 텍스트)."""
    kst = now + timedelta(hours=9)
    days = cfg.strategy.lookback_days
    note = [f"※ LOW% = 최근 {days}일 중 위치 (0%=최저)",
            "※ 1d~20d = 그 거래일 뒤 오른 비율(%)",
            "※ 과거 10년 같은 구간 기준 · 참고용"]
    table = "\n".join(_buy_rows(cfg, quotes, plain=True))
    return f"📍 {kst:%m/%d %H:%M} 환율 (하위% 낮은 순)\n{table}\n" + "\n".join(note)
