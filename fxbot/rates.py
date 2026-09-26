"""원화 환율과 일별 종가 이력을 가져온다.

토스뱅크는 환율우대 100% 라 매매기준율로 사고팔므로, 하나은행 매매기준율(네이버 금융 제공)을 우선 쓰고
조회가 실패한 통화만 Yahoo Finance 시장 중간값으로 대신한다.
"""

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from .config import Currency

HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"}
NAVER = "https://m.stock.naver.com/front-api/marketIndex"
NAVER_PAGE = 60             # 네이버가 한 번에 주는 최대 일수
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


def naver_history(session: requests.Session, cur: Currency, pages: int) -> dict[date, float]:
    """하나은행 매매기준율 일별 종가 (최근 것부터 pages*60 거래일). 값은 1단위당 원화."""
    series: dict[date, float] = {}
    for page in range(1, pages + 1):
        r = session.get(f"{NAVER}/prices", params={"category": "exchange", "reutersCode": f"FX_{cur.code}KRW",
                                                   "page": page, "pageSize": NAVER_PAGE},
                        headers=HEADERS, timeout=15)
        r.raise_for_status()
        rows = r.json()["result"]
        series.update({date.fromisoformat(x["localTradedAt"]): float(x["closePrice"].replace(",", "")) / cur.unit
                       for x in rows})
        if len(rows) < NAVER_PAGE:
            break
    return series


def _naver(session: requests.Session, cur: Currency, lookback_days: int) -> tuple[float, datetime, dict[date, float]]:
    """하나은행 매매기준율. 네이버는 토스와 같은 표시 단위(엔·루피아·동은 100 단위)로 주므로 1단위당으로 되돌린다."""
    try:
        r = session.get(f"{NAVER}/productDetail", params={"category": "exchange", "reutersCode": f"FX_{cur.code}KRW"},
                        headers=HEADERS, timeout=15)
        r.raise_for_status()
        res = r.json()["result"]
        price = float(res["closePrice"].replace(",", "")) / cur.unit
        as_of = datetime.fromisoformat(res["localTradedAt"]).astimezone(timezone.utc)
        return price, as_of, naver_history(session, cur, lookback_days // NAVER_PAGE + 2)
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as e:
        raise RateError(f"{cur.code}: 네이버 {type(e).__name__}") from None


def _usd_cross(session: requests.Session, code: str, cache: dict):
    if "USDKRW=X" not in cache:
        cache["USDKRW=X"] = _chart(session, "USDKRW=X")
    usd_krw, as_of, usd_series = cache["USDKRW=X"]
    usd_x, _, x_series = _chart(session, f"USD{code}=X")
    series = {d: usd_series[d] / x_series[d] for d in usd_series.keys() & x_series.keys()}
    return usd_krw / usd_x, as_of, series


def fetch_quote(session: requests.Session, cur: Currency, lookback_days: int, cache: dict) -> Quote:
    try:
        price, as_of, series = _naver(session, cur, lookback_days)
    except RateError:
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
