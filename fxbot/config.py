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


@dataclass(frozen=True)
class Strategy:
    lookback_days: int = 90


@dataclass(frozen=True)
class Config:
    strategy: Strategy
    currencies: dict[str, Currency] = field(default_factory=dict)


def load(path: Path = CONFIG_PATH) -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    currencies = {
        code: Currency(code=code, name=c.get("name", code),
                       unit=c.get("unit", 1), via_usd=c.get("via_usd", False))
        for code, c in raw["currencies"].items()
    }
    return Config(strategy=Strategy(**raw.get("strategy", {})), currencies=currencies)
