import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"


@dataclass(frozen=True)
class Currency:
    code: str
    name: str
    unit: int = 1
    via_usd: bool = False
    buy_fee: float = 0.0   # 비율 (0.01 = 1%)
    sell_fee: float = 0.0


@dataclass(frozen=True)
class Strategy:
    lookback_days: int = 90
    buy_percentile: float = 20
    add_step_pct: float = 1.5
    max_lots: int = 3
    min_profit_pct: float = 1.0
    lot_krw: int = 1_000_000
    realert_hours: float = 12
    realert_move_pct: float = 0.5


@dataclass(frozen=True)
class Config:
    strategy: Strategy
    currencies: dict[str, Currency] = field(default_factory=dict)


def load(path: Path = CONFIG_PATH) -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    fees = raw.get("fees", {})
    currencies = {}
    for code, c in raw["currencies"].items():
        fee = {**fees, **fees.get(code, {})}
        currencies[code] = Currency(
            code=code,
            name=c.get("name", code),
            unit=c.get("unit", 1),
            via_usd=c.get("via_usd", False),
            buy_fee=fee.get("buy_pct", 0.0) / 100,
            sell_fee=fee.get("sell_pct", 0.0) / 100,
        )
    return Config(strategy=Strategy(**raw.get("strategy", {})), currencies=currencies)
