"""매수·매도 신호 판단.

- 매수: 현재 환율이 최근 lookback 기간 하위 buy_percentile% 이하일 때.
  이미 보유 중이면 가장 싸게 산 환율보다 add_step_pct% 더 내려야 추가 매수 (최대 max_lots 회).
- 매도: 수수료를 모두 빼고도 min_profit_pct% 넘게 이익인 lot 이 있을 때만. 손해 매도 신호는 없다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import Config, Currency, Strategy
from .ledger import Lot
from .rates import Quote


@dataclass(frozen=True)
class Signal:
    side: str               # "buy" | "sell"
    code: str
    price: float            # 현재 환율 (1단위당 원화)
    percentile: float
    low: float
    high: float
    amount: float = 0.0     # 매도 시 팔 수 있는 수량
    profit: float = 0.0     # 매도 시 예상 이익 (원)

    @property
    def key(self) -> str:
        return f"{self.side}:{self.code}"


def percentile(price: float, history: list[float]) -> float:
    """history 중 price 이하인 비율 (0~100)."""
    return 100 * sum(1 for h in history if h <= price) / len(history)


def sell_target(lot: Lot, cur: Currency, s: Strategy) -> float:
    return lot.break_even(cur) * (1 + s.min_profit_pct / 100)


def evaluate(q: Quote, cur: Currency, lots: list[Lot], s: Strategy) -> list[Signal]:
    pct = percentile(q.price, q.history)
    base = dict(code=q.code, price=q.price, percentile=pct, low=min(q.history), high=max(q.history))
    signals = []

    sellable = [l for l in lots if q.price > sell_target(l, cur, s)]
    if sellable:
        amount = sum(l.amount for l in sellable)
        proceeds = amount * q.price * (1 - cur.sell_fee)
        signals.append(Signal("sell", amount=amount, profit=proceeds - sum(l.cost(cur) for l in sellable), **base))

    if pct <= s.buy_percentile and len(lots) < s.max_lots:
        if not lots or q.price <= min(l.rate for l in lots) * (1 - s.add_step_pct / 100):
            signals.append(Signal("buy", **base))
    return signals


def should_alert(sig: Signal, alerts: dict, now: datetime, s: Strategy) -> bool:
    """같은 신호를 매시간 반복해 보내지 않는다. 시간이 충분히 지났거나 환율이 더 유리해졌을 때만 다시 알림."""
    prev = alerts.get(sig.key)
    if not prev:
        return True
    if now - datetime.fromisoformat(prev["ts"]) >= timedelta(hours=s.realert_hours):
        return True
    move = s.realert_move_pct / 100
    if sig.side == "buy":
        return sig.price <= prev["price"] * (1 - move)
    return sig.price >= prev["price"] * (1 + move)


def run(cfg: Config, quotes: dict[str, Quote], lots: dict[str, list[Lot]], alerts: dict, now: datetime) -> list[Signal]:
    """이번에 알릴 신호를 돌려주고 alerts 를 갱신한다. 조건이 풀린 신호는 alerts 에서 지워 다음에 다시 알리게 한다."""
    active, to_send = set(), []
    for code, q in quotes.items():
        for sig in evaluate(q, cfg.currencies[code], lots.get(code, []), cfg.strategy):
            active.add(sig.key)
            if should_alert(sig, alerts, now, cfg.strategy):
                alerts[sig.key] = {"ts": now.isoformat(), "price": sig.price}
                to_send.append(sig)
    for key in [k for k in alerts if k.split(":")[1] in quotes and k not in active]:
        del alerts[key]
    return to_send
