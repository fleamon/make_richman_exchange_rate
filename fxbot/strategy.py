"""알림에 쓰는 환율 위치 계산과 발송 주기 판단.

싸다/비싸다를 봇이 정하지 않는다 — 전 통화를 하위 % 낮은 순으로 보여주고 판단은 사람이 한다.
"""

from datetime import datetime, timedelta

# 앞으로 오를 가능성이 높다고 보는 순서 (기축·안전통화 → 선진국 → 신흥국). 같은 하위 %끼리의 정렬에 쓴다.
PRIORITY = ("USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "SGD", "HKD", "NZD",
            "CNY", "TWD", "MYR", "THB", "PHP", "IDR", "VND")


def percentile(price: float, history: list[float]) -> float:
    """history 중 price 보다 낮은 값의 비율 (0~100). 기간 최저면 0%."""
    # 야후 현재가는 값이 작은 통화(루피아 등)에서 소수 4자리로 반올림돼 오고 종가는 float32 오차가 있다
    # → 0.1% 이내 차이는 같은 값으로 본다 (그래야 오늘이 최저일 때 0% 로 나온다)
    return 100 * sum(1 for h in history if h < price * (1 - 1e-3)) / len(history)


def signal_slot(now: datetime) -> str:
    """알림은 한국 시각 기준 1시간에 한 번만 보낸다. 같은 시간대의 두 번째 실행은 명령만 처리한다."""
    return (now + timedelta(hours=9)).strftime("%Y-%m-%dT%H")
