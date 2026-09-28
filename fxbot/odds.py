"""하위 % 구간별 '거래일 N일 뒤 오를 확률' — 과거 환율 백테스트(fxbot/odds.json)로 만든 표를 읽어 쓴다.

표는 `python -m fxbot backtest` 로 다시 만든다. 통화별 표본이 적으므로 전 통화 공통 확률 쪽으로 당겨서(shrinkage) 쓴다.
"""

import json
from datetime import date
from pathlib import Path

from .strategy import percentile

PATH = Path(__file__).resolve().parent / "odds.json"
EDGES = (5, 10, 20, 40)      # 하위 % 구간 경계: ~5 / 5~10 / 10~20 / 20~40 / 40~
HORIZONS = (1, 3, 5, 10, 20)  # 거래일 (20 ≈ 30일)
RANK_HORIZON = 5             # 매수 알림 정렬 기준 구간
EDGE_HORIZON = 20            # '저점 반등' 판단 기준 구간
PRIOR = 400                  # 통화별 표본이 이만큼 있어야 공통 확률과 반반 (표본이 겹쳐서 실제 독립 표본은 훨씬 적다)


def bucket(pct: float) -> int:
    return sum(pct > e for e in EDGES)


def bucket_name(b: int) -> str:
    lo, hi = ([0, *EDGES][b], [*EDGES, 100][b])
    return f"하위 {lo}~{hi}%"


def load() -> dict:
    return json.loads(PATH.read_text()) if PATH.exists() else {}


def probability(table: dict, code: str, pct: float, horizon: int = RANK_HORIZON) -> float | None:
    """지금 하위 pct% 인 code 통화가 horizon 거래일 뒤 오를 확률 (0~1). 표가 없으면 None."""
    h, b = str(horizon), str(bucket(pct))
    pooled = (table.get("pooled", {}).get(h) or {}).get(b) if table else None
    if not pooled:
        return None
    pooled_p, _ = pooled
    p, n = (table["currency"].get(code, {}).get(h) or {}).get(b, (pooled_p, 0))
    w = n / (n + PRIOR)
    return w * p + (1 - w) * pooled_p


def probabilities(table: dict, code: str, pct: float) -> dict[int, float]:
    """구간별 상승 확률 {거래일: 확률}. 표가 없는 구간은 빠진다."""
    got = {h: probability(table, code, pct, h) for h in HORIZONS}
    return {h: p for h, p in got.items() if p is not None}


def dip_edge(table: dict, code: str) -> str | None:
    """이 통화는 하위 10% 이하 저점에서 과거에 얼마나 잘 올랐나: 강함(60%↑) / 보통 / 약함(52%↓)."""
    per_h = (table.get("currency", {}).get(code, {}) or {}).get(str(EDGE_HORIZON), {}) if table else {}
    cells = [c for c in (per_h.get(b) for b in ("0", "1")) if c]
    if not cells:
        return None
    p = sum(c[0] * c[1] for c in cells) / sum(c[1] for c in cells)
    return "강함" if p >= 0.60 else "약함" if p <= 0.52 else "보통"


def build(history: dict[str, list[tuple[date, float]]], lookback_days: int) -> dict:
    """history: 통화 → (날짜, 1단위당 원화) 오래된 순. 각 날짜에서 구간별로 HORIZONS 거래일 뒤 올랐는지 센다."""
    from datetime import timedelta
    import bisect

    # cell[통화][거래일][구간] = [오른 횟수, 전체 횟수]
    cell: dict[str, dict[int, dict[int, list[int]]]] = {}
    for code, rows in history.items():
        days = [d for d, _ in rows]
        counts = cell.setdefault(code, {h: {} for h in HORIZONS})
        for i in range(len(rows) - min(HORIZONS)):
            t, price = rows[i]
            j = bisect.bisect_left(days, t - timedelta(days=lookback_days))
            window = [v for _, v in rows[j : i + 1]]
            if len(window) < lookback_days * 0.55:
                continue
            b = bucket(percentile(price, window))
            for h in HORIZONS:
                if i + h >= len(rows):
                    continue
                c = counts[h].setdefault(b, [0, 0])
                c[0] += rows[i + h][1] > price
                c[1] += 1
    pooled: dict[int, dict[int, list[int]]] = {h: {} for h in HORIZONS}
    for counts in cell.values():
        for h, per_b in counts.items():
            for b, (up, n) in per_b.items():
                acc = pooled[h].setdefault(b, [0, 0])
                acc[0] += up
                acc[1] += n
    first = min(d for rows in history.values() for d, _ in rows)
    last = max(d for rows in history.values() for d, _ in rows)
    rate = lambda per_b: {str(b): [round(u / n, 4), n] for b, (u, n) in sorted(per_b.items())}
    return {
        "lookback_days": lookback_days, "horizons": list(HORIZONS), "period": [first.isoformat(), last.isoformat()],
        "pooled": {str(h): rate(per_b) for h, per_b in pooled.items()},
        "currency": {code: {str(h): rate(per_b) for h, per_b in counts.items()} for code, counts in cell.items()},
    }
