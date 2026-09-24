"""거래 기록(trades) 을 선입선출로 재생해 현재 보유 lot 을 만든다.

rate 는 모두 외화 1단위당 원화로 저장한다 (토스 표시 단위 변환은 입력·출력에서만).
"""

from dataclasses import dataclass

from .config import Currency


@dataclass
class Lot:
    code: str
    rate: float      # 매수 환율 (1단위당 원화)
    amount: float    # 남은 외화 수량
    ts: str

    def cost(self, cur: Currency) -> float:
        """수수료 포함 매수 원가 (원)."""
        return self.amount * self.rate * (1 + cur.buy_fee)

    def break_even(self, cur: Currency) -> float:
        """수수료를 모두 빼고 본전이 되는 매도 환율."""
        return self.rate * (1 + cur.buy_fee) / (1 - cur.sell_fee)


@dataclass(frozen=True)
class SellResult:
    proceeds: float   # 수수료 차감 후 받는 원화
    cost: float       # 팔린 lot 들의 원가
    @property
    def profit(self) -> float:
        return self.proceeds - self.cost


class LedgerError(Exception):
    pass


def replay(trades: list[dict], currencies: dict[str, Currency]) -> dict[str, list[Lot]]:
    lots: dict[str, list[Lot]] = {}
    for t in trades:
        if t["side"] == "buy":
            lots.setdefault(t["code"], []).append(Lot(t["code"], t["rate"], t["amount"], t["ts"]))
        else:
            _consume(lots.get(t["code"], []), t, currencies[t["code"]])
    return {code: ls for code, ls in lots.items() if ls}


def _consume(lots: list[Lot], trade: dict, cur: Currency) -> SellResult:
    remaining = trade["amount"]
    if remaining > sum(l.amount for l in lots) + 1e-9:
        raise LedgerError(f"{trade['code']} 보유량보다 많이 팔 수 없습니다.")
    cost = 0.0
    while remaining > 1e-9:
        lot = lots[0]
        take = min(lot.amount, remaining)
        cost += take * lot.rate * (1 + cur.buy_fee)
        lot.amount -= take
        remaining -= take
        if lot.amount <= 1e-9:
            lots.pop(0)
    return SellResult(proceeds=trade["amount"] * trade["rate"] * (1 - cur.sell_fee), cost=cost)


def preview_sell(trades: list[dict], trade: dict, currencies: dict[str, Currency]) -> SellResult:
    """trade 를 기록하기 전에 손익을 계산한다 (보유량 부족이면 LedgerError)."""
    lots = replay(trades, currencies)
    return _consume(lots.get(trade["code"], []), trade, currencies[trade["code"]])
