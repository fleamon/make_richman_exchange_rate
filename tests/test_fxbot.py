from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet

from fxbot import bot, config, state, strategy
from fxbot.config import Config, Currency, Strategy
from fxbot.rates import Quote

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
USD = Currency("USD", "미국 달러")
JPY = Currency("JPY", "일본 엔", unit=100)
CFG = Config(Strategy(lookback_days=180), {"USD": USD, "JPY": JPY})


def quote(code, price, lo=1300.0, hi=1500.0, n=90):
    step = (hi - lo) / (n - 1)
    return Quote(code, price, [lo + i * step for i in range(n)], NOW)


def test_config_loads_all_toss_currencies():
    cfg = config.load()
    assert len(cfg.currencies) == 17
    assert cfg.currencies["JPY"].unit == 100 and cfg.currencies["VND"].via_usd


def test_percentile_is_zero_at_period_low():
    assert strategy.percentile(1300.0, quote("USD", 1300).history) == 0
    assert strategy.percentile(1500.0, quote("USD", 1500).history) > 98


def test_bot_only_answers_rates_signal_and_help():
    quotes = {"JPY": quote("JPY", 9.0, 8, 10)}
    assert "900.00" in bot.handle("환율", CFG, quotes, NOW)         # 토스 표시 단위(100엔)
    assert "조회 실패" in bot.handle("/rates", CFG, {}, NOW)
    assert "LOW%  \u20071d  \u20073d" in bot.handle("신호", CFG, quotes, NOW)
    for text in ("매수 USD 1350 1000", "현황", "기록", "취소", "아무말", ""):
        assert bot.handle(text, CFG, quotes, NOW) == bot.HELP


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_KEY", Fernet.generate_key().decode())
    path = tmp_path / "state.enc"
    st = {**state.empty(), "tg_offset": 42, "signal_slot": "2026-09-25T12"}
    state.save(st, path)
    assert b"signal_slot" not in path.read_bytes()
    assert state.load(path) == st
    size = path.stat().st_size
    state.save({**st, "tg_offset": 10 ** 12}, path)
    assert path.stat().st_size == size  # 내용이 늘어도 파일 크기로 드러나지 않음


def test_signal_slot_is_hourly_kst():
    base = datetime(2026, 9, 25, 3, 7, tzinfo=timezone.utc)  # 한국 12:07
    assert strategy.signal_slot(base) == strategy.signal_slot(base + timedelta(minutes=30))
    assert strategy.signal_slot(base) != strategy.signal_slot(base + timedelta(minutes=60))
    assert strategy.signal_slot(base) == "2026-09-25T12"


def test_report_has_only_time_and_buy_table():
    quotes = {"USD": quote("USD", 1310), "JPY": quote("JPY", 9.0, 8, 10)}
    text = bot.report_text(NOW, config.load(), quotes)
    assert text.startswith("📍 09/24 09:00 환율")
    assert "보유" not in text and "매도" not in text
    assert "<pre>" not in text                                     # 코드 블록 없이
    assert "<code>" not in text and "&" not in text                # 인라인 코드·HTML 없이 일반 텍스트
    assert any(l.startswith("🇺🇸 ") and l.endswith("  USD") for l in text.split("\n"))  # 국기 앞, 코드 끝


def test_buy_text_is_table_sorted_by_percentile(monkeypatch):
    from fxbot import odds
    cfg = config.load()
    pooled = {str(b): [0.5, 1000] for b in range(5)}
    table = {"horizons": list(odds.HORIZONS),
             "pooled": {str(h): pooled for h in odds.HORIZONS},
             "currency": {"EUR": {str(h): {"0": [0.7, 100000]} for h in odds.HORIZONS},
                          "USD": {str(h): {"0": [0.4, 100000]} for h in odds.HORIZONS}}}
    monkeypatch.setattr(odds, "load", lambda: table)
    hist = [100.0 + i for i in range(60)]
    mk = lambda c, p: Quote(c, p, hist, NOW)
    quotes = {"USD": mk("USD", 100), "EUR": mk("EUR", 100), "JPY": mk("JPY", 100), "GBP": mk("GBP", 159)}
    body = bot._buy_rows(cfg, quotes)
    assert [l.split()[-1] for l in body[1:]] == ["EUR", "JPY", "USD", "GBP"]  # 하위 0% 셋(확률 순) → 하위 98%
    assert body[0].replace(bot.FS, " ").split() == [bot.HEADER_ICON, "RATE", "LOW%"] + [f"{h}d" for h in odds.HORIZONS] + ["CCY"]
    assert body[1].startswith(bot.flag("EUR") + " ")


def test_buy_table_columns_are_aligned(monkeypatch):
    from fxbot import odds
    cfg = config.load()
    monkeypatch.setattr(odds, "load", lambda: {})
    hist = [100.0 + i for i in range(60)]
    quotes = {c: Quote(c, 100.0 + i, hist, NOW) for i, c in enumerate(("USD", "JPY", "IDR"))}
    body = bot._buy_rows(cfg, quotes)
    nums = [l.split(" ", 1)[1].rsplit("  ", 1)[0] for l in body[1:]]  # 국기·통화 코드를 뺀 숫자 열
    assert len({len(n) for n in nums}) == 1                           # 숫자 열 글자 수가 줄마다 같다
    assert all(set(n) <= set("0123456789.%- " + bot.FS) for n in nums)  # 숫자·숫자 폭 공백만 (쉼표 없음)


def test_probability_shrinks_toward_pooled():
    from fxbot import odds
    h = str(odds.RANK_HORIZON)
    table = {"pooled": {h: {"0": [0.5, 1000]}},
             "currency": {"X": {h: {"0": [0.9, 400]}}, "Y": {h: {"0": [0.9, 4]}}}}
    assert abs(odds.probability(table, "X", 0) - 0.7) < 1e-9
    assert abs(odds.probability(table, "Y", 0) - 0.5) < 0.01
    assert odds.probability(table, "Z", 0) == 0.5
    assert odds.probability({}, "X", 0) is None
    assert odds.probability(table, "X", 0, horizon=1) is None       # 표에 없는 구간
    assert odds.probabilities(table, "X", 0) == {odds.RANK_HORIZON: 0.7}


def test_build_counts_every_horizon():
    from datetime import date
    from fxbot import odds
    day0 = date(2020, 1, 1)
    rows = [(day0 + timedelta(days=i), 100.0 + i) for i in range(400)]   # 계속 오르는 환율
    table = odds.build({"USD": rows}, 180)
    assert table["horizons"] == list(odds.HORIZONS)
    counted = {}
    for h in odds.HORIZONS:
        cells = table["currency"]["USD"][str(h)]
        assert cells, h
        assert all(p == 1.0 for p, _ in cells.values())                  # 오름 추세면 모두 상승
        counted[h] = sum(n for _, n in cells.values())
    assert counted[1] > counted[20] > 0                                  # 먼 구간일수록 셀 수 있는 날이 적다
