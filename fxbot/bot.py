"""텔레그램 명령 처리와 메시지 문구.

환율은 토스 앱과 같은 단위로 주고받는다 (엔·루피아·동은 100 단위).
"""

import math
from datetime import datetime

from .config import Config, Currency
from .ledger import LedgerError, preview_sell, replay
from .rates import Quote
from .strategy import Signal, percentile, sell_target

HELP = """사용법 (환율은 토스 앱 표시 그대로, 수량은 외화 금액)
매수 USD 1350.5 1000   — 1,350.5원에 1,000달러 샀음
매도 JPY 905.2 100000  — 100엔당 905.2원에 10만엔 팔았음
현황   — 보유 외화, 본전·목표 환율, 평가손익
환율   — 전체 통화 현재 환율과 3개월 위치
기록   — 최근 거래 10건
취소   — 마지막 거래 기록 삭제
(/buy /sell /status /rates /history /undo 도 가능)"""

ALIASES = {
    "매수": "buy", "buy": "buy",
    "매도": "sell", "sell": "sell",
    "현황": "status", "status": "status",
    "환율": "rates", "rates": "rates",
    "기록": "history", "history": "history",
    "취소": "undo", "undo": "undo",
    "도움말": "help", "help": "help", "start": "help",
}


def won(v: float) -> str:
    return f"{v:,.0f}원"


def fx(v: float, cur: Currency) -> str:
    """1단위당 원화 → 토스 표시 단위 문자열."""
    q = v * cur.unit
    return f"{q:,.2f}" if q >= 1 else f"{q:,.4f}"


def label(cur: Currency) -> str:
    return f"{cur.code}({cur.name})"


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
        if len(parts) != 4 or parts[1].upper() not in cfg.currencies:
            return f"형식: {parts[0]} 통화 환율 수량\n예) 매수 USD 1350.5 1000\n지원 통화: {' '.join(cfg.currencies)}"
        cur = cfg.currencies[parts[1].upper()]
        try:
            rate, amount = _num(parts[2]) / cur.unit, _num(parts[3])
        except ValueError:
            return "환율과 수량은 0보다 큰 숫자로 적어주세요."
        trade = {"side": cmd, "code": cur.code, "rate": rate, "amount": amount, "ts": now.isoformat(timespec="seconds")}
        if cmd == "buy":
            trades.append(trade)
            lots = replay(trades, cfg.currencies)[cur.code]
            target = sell_target(lots[-1], cur, cfg.strategy)
            return (f"매수 기록 완료: {label(cur)} {amount:,.2f} @ {fx(rate, cur)} (원가 {won(lots[-1].cost(cur))})\n"
                    f"매도 목표 환율: {fx(target, cur)} 이상")
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

    if cmd == "history":
        if not trades:
            return "거래 기록이 없습니다."
        lines = [f"{t['ts'][:16].replace('T', ' ')} {'매수' if t['side'] == 'buy' else '매도'} {t['code']} "
                 f"{t['amount']:,.2f} @ {fx(t['rate'], cfg.currencies[t['code']])}" for t in trades[-10:]]
        return "최근 거래 (UTC)\n" + "\n".join(lines)

    if cmd == "status":
        return status_text(state, cfg, quotes)

    if cmd == "rates":
        return rates_text(cfg, quotes)

    return HELP


def status_text(state: dict, cfg: Config, quotes: dict[str, Quote]) -> str:
    lots = replay(state["trades"], cfg.currencies)
    if not lots:
        return "보유 중인 외화가 없습니다."
    lines, total = ["보유 현황"], 0.0
    for code, ls in lots.items():
        cur, q = cfg.currencies[code], quotes.get(code)
        amount, cost = sum(l.amount for l in ls), sum(l.cost(cur) for l in ls)
        head = f"\n{label(cur)} {amount:,.2f} / 원가 {won(cost)}"
        if q:
            pnl = amount * q.price * (1 - cur.sell_fee) - cost
            total += pnl
            head += f"\n  현재 {fx(q.price, cur)} → 평가손익 {won(pnl)}"
        lines.append(head)
        for l in ls:
            lines.append(f"  · {l.amount:,.2f} @ {fx(l.rate, cur)} → 목표 {fx(sell_target(l, cur, cfg.strategy), cur)}")
    lines.append(f"\n총 평가손익 {won(total)}")
    return "\n".join(lines)


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


def signal_text(sig: Signal, cur: Currency, cfg: Config) -> str:
    rng = f"최근 {cfg.strategy.lookback_days}일 {fx(sig.low, cur)} ~ {fx(sig.high, cur)}, 현재 하위 {sig.percentile:.0f}%"
    if sig.side == "buy":
        return (f"🟢 매수 신호 {label(cur)}\n현재 {fx(sig.price, cur)} ({rng})\n"
                f"매수 시 '매수 {cur.code} 환율 수량' 으로 기록해주세요.")
    return (f"🔴 매도 신호 {label(cur)}\n현재 {fx(sig.price, cur)} ({rng})\n"
            f"목표 도달 수량 {sig.amount:,.2f} {cur.code}, 예상 이익 {won(sig.profit)} (수수료 차감 후)\n"
            f"매도 시 '매도 {cur.code} 환율 수량' 으로 기록해주세요.")
