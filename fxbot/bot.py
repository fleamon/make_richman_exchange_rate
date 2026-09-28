"""텔레그램 명령 처리와 메시지 문구.

환율은 토스 앱과 같은 단위로 주고받는다 (엔·루피아·동은 100 단위).
"""

import html
import math
import unicodedata
from datetime import datetime, timedelta

from . import odds
from .config import Config, Currency
from .ledger import LedgerError, preview_sell, replay
from .rates import Quote
from .strategy import PRIORITY, Signal, percentile, sell_target, sort_signals

HELP = """사용법 (환율은 토스 앱 표시 그대로, 수량은 외화 금액)
매수 USD 1350.5 1000   — 1,350.5원에 1,000달러 샀음
매도 JPY 905.2 100000  — 100엔당 905.2원에 10만엔 팔았음
매도 JPY 905.2 전량    — 100엔당 905.2원에 가진 엔화 전부 팔았음
매도 JPY 전량매도      — 가진 엔화 전부 팔았음 (환율은 봇 처리 시점 시장 환율)
현황   — 보유 외화별 수량, 평균 매수 환율, 원가, 평가손익
환율   — 전체 통화 현재 환율과 3개월 위치
취소   — 마지막 거래 기록 삭제
(/buy /sell /status /rates /undo 도 가능)"""

ALIASES = {
    "매수": "buy", "buy": "buy",
    "매도": "sell", "sell": "sell",
    "현황": "status", "status": "status",
    "환율": "rates", "rates": "rates",
    "기록": "status", "history": "status",
    "취소": "undo", "undo": "undo",
    "도움말": "help", "help": "help", "start": "help",
}
ALL = ("전량", "전량매도", "all")


def won(v: float) -> str:
    return f"{v:,.0f}원"


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


TABLE_WIDTH = 36   # 모든 표의 줄 폭을 같게 둬야 텔레그램이 코드 블록을 같은 글꼴 크기로 그린다


def _table(rows: list[str]) -> str:
    """고정폭 표. 줄 끝 공백은 텔레그램이 버리므로 마지막 칸은 항상 숫자로 끝나게 오른쪽 정렬한다."""
    body = "\n".join(_pad(r.rstrip(), TABLE_WIDTH) for r in rows)
    return f"<pre>{html.escape(body, quote=False)}</pre>"


def _num(s: str) -> float:
    v = float(s.replace(",", ""))
    if not math.isfinite(v) or v <= 0:
        raise ValueError
    return v


def handle(text: str, state: dict, cfg: Config, quotes: dict[str, Quote], now: datetime) -> str:
    parts = text.strip().lstrip("/").split()
    if not parts:
        return HELP
    cmd = ALIASES.get(parts[0].split("@")[0].lower())
    trades = state["trades"]

    if cmd in ("buy", "sell"):
        # 매도는 수량 대신 '전량(매도)' 을 쓸 수 있고, 환율까지 생략하면 처리 시점의 시장 환율로 기록한다
        sell_all = cmd == "sell" and len(parts) in (3, 4) and parts[-1] in ALL
        if len(parts) != 4 - (sell_all and len(parts) == 3) or parts[1].upper() not in cfg.currencies:
            return (f"형식: {parts[0]} 통화 환율 수량\n예) 매수 USD 1350.5 1000 / 매도 JPY 전량매도\n"
                    f"지원 통화: {' '.join(cfg.currencies)}")
        cur = cfg.currencies[parts[1].upper()]
        held = sum(l.amount for l in replay(trades, cfg.currencies).get(cur.code, []))
        if sell_all and not held:
            return f"{label(cur)} 보유 기록이 없습니다."
        try:
            if sell_all and len(parts) == 3:
                if cur.code not in quotes:
                    return "지금 환율을 가져오지 못했습니다. '매도 통화 환율 전량' 으로 환율을 적어주세요."
                rate = quotes[cur.code].price
            else:
                rate = _num(parts[2]) / cur.unit
            amount = held if sell_all else _num(parts[3])
        except ValueError:
            return "환율과 수량은 0보다 큰 숫자로 적어주세요."
        trade = {"side": cmd, "code": cur.code, "rate": rate, "amount": amount, "ts": now.isoformat(timespec="seconds")}
        if cmd == "buy":
            trades.append(trade)
            lots = replay(trades, cfg.currencies)[cur.code]
            target = sell_target(lots[-1], cur, cfg.strategy)
            return (f"매수 기록 완료: {label(cur)} {amount:,.2f} @ {fx(rate, cur)} (원가 {won(lots[-1].cost(cur))})\n"
                    f"매도 목표 환율: {fx(target, cur)} 초과")
        try:
            result = preview_sell(trades, trade, cfg.currencies)
        except LedgerError as e:
            return str(e)
        trades.append(trade)
        warn = "\n⚠️ 손해 매도로 기록되었습니다." if result.profit < 0 else ""
        return f"매도 기록 완료: {label(cur)} {amount:,.2f} @ {fx(rate, cur)}\n실현손익 {won(result.profit)}{warn}"

    if cmd == "undo":
        if not trades:
            return "취소할 기록이 없습니다."
        t = trades.pop()
        cur = cfg.currencies[t["code"]]
        return f"마지막 기록 삭제: {'매수' if t['side'] == 'buy' else '매도'} {t['code']} {t['amount']:,.2f} @ {fx(t['rate'], cur)}"

    if cmd == "status":  # '기록' 도 같은 답을 준다
        return status_text(state, cfg, quotes)

    if cmd == "rates":
        return rates_text(cfg, quotes)

    return HELP


def status_text(state: dict, cfg: Config, quotes: dict[str, Quote]) -> str:
    """보유 통화를 고정폭 표로 (수량 / 평균 매수 환율 / 현재 환율 / 평가손익)."""
    lots = replay(state["trades"], cfg.currencies)
    if not lots:
        return "보유 중인 외화가 없습니다."
    body, total, cost_sum = [], 0.0, 0.0
    for code, ls in lots.items():
        cur, q = cfg.currencies[code], quotes.get(code)
        amount, cost = sum(l.amount for l in ls), sum(l.cost(cur) for l in ls)
        avg = sum(l.rate * l.amount for l in ls) / amount
        cost_sum += cost
        pnl = amount * q.price * (1 - cur.sell_fee) - cost if q else None
        total += pnl or 0.0
        body.append("".join([_pad(f"{flag(code)} {code}", 7, right=False),
                             _pad(f"{amount:,.2f}", 10), _pad(fx(avg, cur), 9),
                             _pad(f"{pnl:+,.0f}" if pnl is not None else "-", 10)]))
    return ("💰 보유 현황\n"
            "(수량 / 평균 매수가 / 평가손익)\n\n"
            f"{_table(body)}\n\n"
            f"총 원가 {won(cost_sum)}\n"
            f"총 평가손익 {won(total)}")


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


def header_text(now: datetime) -> str:
    kst = now + timedelta(hours=9)
    return f"━━━━━━━━━━━━━━━\n📍 {kst:%m/%d %H:%M} 최신 신호\n(이 메시지 아래가 가장 최근 알림입니다)\n━━━━━━━━━━━━━━━"


def buy_text(cfg: Config, quotes: dict[str, Quote]) -> str:
    """전 통화를 하위 % 오름차순 고정폭 표로 (같은 %는 단기 확률 높은 순).

    표 안에는 한글을 넣지 않는다 — 휴대폰 고정폭 글꼴에서 한글 폭이 일정하지 않아 열이 밀린다.
    """
    days = cfg.strategy.lookback_days
    table = odds.load()
    rank = {c: i for i, c in enumerate(PRIORITY)}
    rows = []
    for q in quotes.values():
        pct = percentile(q.price, q.history)
        rows.append((odds.probabilities(table, q.code, pct), pct, q))
    rows.sort(key=lambda r: (round(r[1]), -(r[0].get(odds.RANK_HORIZON) or 0), rank.get(r[2].code, len(rank))))

    body = [" " * 20 + "".join(_pad(str(h), 4 if i == 0 else 3) for i, h in enumerate(odds.HORIZONS))]
    for probs, pct, q in rows:
        cells = [_pad(f"{flag(q.code)} {q.code}", 7, right=False),
                 _pad(fx(q.price, cfg.currencies[q.code]), 9), _pad(f"{pct:.0f}%", 4)]
        cells += [_pad(f"{probs[h] * 100:.0f}" if h in probs else "-", 4 if i == 0 else 3)
                  for i, h in enumerate(odds.HORIZONS)]
        body.append("".join(cells))

    note = [f"※ 하위 % = 최근 {days}일 중 위치 (0%=최저)",
            "※ 1~20 = 그 거래일 뒤 오른 비율(%)",
            "※ 과거 10년 같은 구간 기준 · 참고용",
            "기록: '매수 통화 환율 수량'"]
    return (f"🟢 매수 신호 · {len(rows)}개 통화\n\n{_table(body)}\n\n"
            + html.escape("\n".join(note), quote=False))


def signals_text(side: str, signals: list[Signal], cfg: Config) -> str:
    """매도 신호를 고정폭 표 하나로 (환율 / 하위 % / 예상 이익). 하위 % 오름차순."""
    sigs = sort_signals([s for s in signals if s.side == side])
    if not sigs:
        return "🔴 매도 신호 없음"
    body = ["".join([_pad(f"{flag(sig.code)} {sig.code}", 7, right=False),
                     _pad(fx(sig.price, cfg.currencies[sig.code]), 9),
                     _pad(f"{sig.percentile:.0f}%", 4), _pad(f"{sig.profit:+,.0f}", 16)])
            for sig in sigs]
    return (f"🔴 매도 신호 {len(sigs)}건\n"
            "(환율 / 하위 % / 예상 이익)\n\n"
            f"{_table(body)}\n\n"
            "기록: '매도 통화 환율 수량'")


def signal_text(sig: Signal, cur: Currency, cfg: Config) -> str:
    rng = f"최근 {cfg.strategy.lookback_days}일 {fx(sig.low, cur)} ~ {fx(sig.high, cur)}, 현재 하위 {sig.percentile:.0f}%"
    if sig.side == "buy":
        return (f"🟢 매수 신호 {label(cur)}\n현재 {fx(sig.price, cur)} ({rng})\n"
                f"매수 시 '매수 {cur.code} 환율 수량' 으로 기록해주세요.")
    return (f"🔴 매도 신호 {label(cur)}\n현재 {fx(sig.price, cur)} ({rng})\n"
            f"예상 이익 {won(sig.profit)}\n"
            f"매도 시 '매도 {cur.code} 환율 수량' 으로 기록해주세요.")
