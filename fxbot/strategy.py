"""매수·매도 신호 판단.

- 매수: 현재 환율이 최근 lookback 기간 하위 buy_percentile% 이하일 때.
  이미 보유 중이면 보유분 평균 매수 환율보다 쌀 때만 (횟수 제한 없음).
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


# 앞으로 오를 가능성이 높다고 보는 순서 (기축·안전통화 → 선진국 → 신흥국)
PRIORITY = ("USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "SGD", "HKD", "NZD",
            "CNY", "TWD", "MYR", "THB", "PHP", "IDR", "VND")


def sort_signals(signals: list[Signal]) -> list[Signal]:
    """오를 가능성 높은 통화 순, 같은 등급 안에서는 하위 퍼센트 오름차순."""
    rank = {c: i for i, c in enumerate(PRIORITY)}
    return sorted(signals, key=lambda s: (rank.get(s.code, len(rank)), s.percentile))


def percentile(price: float, history: list[float]) -> float:
    """history 중 price 보다 낮은 값의 비율 (0~100). 기간 최저면 0%."""
    # 야후 현재가는 값이 작은 통화(루피아 등)에서 소수 4자리로 반올림돼 오고 종가는 float32 오차가 있다
    # → 0.1% 이내 차이는 같은 값으로 본다 (그래야 오늘이 최저일 때 0% 로 나온다)
    return 100 * sum(1 for h in history if h < price * (1 - 1e-3)) / len(history)


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

    if pct <= s.buy_percentile:
        if not lots or q.price < sum(l.rate * l.amount for l in lots) / sum(l.amount for l in lots):
            signals.append(Signal("buy", **base))
    return signals


def run(cfg: Config, quotes: dict[str, Quote], lots: dict[str, list[Lot]]) -> list[Signal]:
    """지금 조건에 맞는 신호 전부 (같은 신호도 매번 보낸다)."""
    return [sig for code, q in quotes.items() for sig in evaluate(q, cfg.currencies[code], lots.get(code, []), cfg.strategy)]


def signal_slot(now: datetime) -> str:
    """신호는 한국 시각 기준 1시간에 한 번만 보낸다. 같은 시간대의 두 번째 실행은 명령만 처리한다."""
    return (now + timedelta(hours=9)).strftime("%Y-%m-%dT%H")
