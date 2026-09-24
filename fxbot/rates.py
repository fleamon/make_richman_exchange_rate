"""Yahoo Finance 차트 API 로 원화 환율(시장 중간값)과 일별 종가 이력을 가져온다.

토스뱅크는 환율우대 100% 라 매매기준율로 사고팔며, 매매기준율은 시장 중간값을 따라간다.
"""

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from .config import Currency

HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"}
MIN_POINTS = 30


@dataclass(frozen=True)
class Quote:
    code: str
    price: float                 # 외화 1단위당 원화
    history: list[float]         # lookback 기간 일별 종가 (오래된 순)
    as_of: datetime


class RateError(Exception):
    pass


def _chart(session: requests.Session, symbol: str) -> tuple[float, datetime, dict[date, float]]:
    last_err = None
    for attempt, host in enumerate(HOSTS * 2):
        try:
            r = session.get(
                f"https://{host}/v8/finance/chart/{symbol}",
                params={"range": "1y", "interval": "1d"},
                headers=HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            closes = res["indicators"]["quote"][0].get("close") or []
            series = {
                datetime.fromtimestamp(t, tz=timezone.utc).date(): c
                for t, c in zip(res.get("timestamp") or [], closes)
                if c
            }
            meta = res["meta"]
            as_of = datetime.fromtimestamp(meta["regularMarketTime"], tz=timezone.utc)
            return float(meta["regularMarketPrice"]), as_of, series
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RateError(f"{symbol}: {last_err}")


def _usd_cross(session: requests.Session, code: str, cache: dict):
    if "USDKRW=X" not in cache:
        cache["USDKRW=X"] = _chart(session, "USDKRW=X")
    usd_krw, as_of, usd_series = cache["USDKRW=X"]
    usd_x, _, x_series = _chart(session, f"USD{code}=X")
    series = {d: usd_series[d] / x_series[d] for d in usd_series.keys() & x_series.keys()}
    return usd_krw / usd_x, as_of, series


def fetch_quote(session: requests.Session, cur: Currency, lookback_days: int, cache: dict) -> Quote:
    if cur.via_usd:
        price, as_of, series = _usd_cross(session, cur.code, cache)
    else:
        price, as_of, series = _chart(session, f"{cur.code}KRW=X")
    since = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
    history = [v for d, v in sorted(series.items()) if d >= since]
    if len(history) < MIN_POINTS:
        raise RateError(f"{cur.code}: 이력 부족 ({len(history)}일)")
    return Quote(code=cur.code, price=price, history=history, as_of=as_of)


def fetch_all(currencies: dict[str, Currency], lookback_days: int) -> tuple[dict[str, Quote], list[str]]:
    quotes, errors, cache = {}, [], {}
    with requests.Session() as session:
        for code, cur in currencies.items():
            try:
                quotes[code] = fetch_quote(session, cur, lookback_days, cache)
            except RateError as e:
                errors.append(str(e))
    return quotes, errors
