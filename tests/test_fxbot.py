from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from fxbot import bot, config, state, strategy
from fxbot.config import Config, Currency, Strategy
from fxbot.ledger import LedgerError, preview_sell, replay
from fxbot.rates import Quote

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
USD = Currency("USD", "미국 달러")
JPY = Currency("JPY", "일본 엔", unit=100)
FEE = Currency("USD", "미국 달러", buy_fee=0.01, sell_fee=0.01)
CFG = Config(Strategy(buy_percentile=20), {"USD": USD, "JPY": JPY})


def quote(code, price, lo=1300.0, hi=1500.0, n=90):
    step = (hi - lo) / (n - 1)
    return Quote(code, price, [lo + i * step for i in range(n)], NOW)


def buy(code, rate, amount):
    return {"side": "buy", "code": code, "rate": rate, "amount": amount, "ts": NOW.isoformat()}


def sell(code, rate, amount):
    return {**buy(code, rate, amount), "side": "sell"}


def test_config_loads_all_toss_currencies():
    cfg = config.load()
    assert len(cfg.currencies) == 17
    assert cfg.currencies["JPY"].unit == 100 and cfg.currencies["VND"].via_usd


def test_sell_fifo_and_profit():
    trades = [buy("USD", 1300, 100), buy("USD", 1350, 100)]
    r = preview_sell(trades, sell("USD", 1400, 150), {"USD": USD})
    assert r.cost == pytest.approx(100 * 1300 + 50 * 1350)
    assert r.profit == pytest.approx(150 * 1400 - r.cost)
    lots = replay(trades + [sell("USD", 1400, 150)], {"USD": USD})["USD"]
    assert [(l.rate, l.amount) for l in lots] == [(1350, 50)]


def test_cannot_oversell():
    with pytest.raises(LedgerError):
        preview_sell([buy("USD", 1300, 100)], sell("USD", 1400, 101), {"USD": USD})


def test_sell_target_covers_fees_and_min_profit():
    lot = replay([buy("USD", 1000, 1)], {"USD": FEE})["USD"][0]
    target = strategy.sell_target(lot, FEE, Strategy(min_profit_pct=1.0))
    # 목표 환율에 팔면 수수료를 다 내고도 원가 대비 1% 이상 이익
    assert target * (1 - FEE.sell_fee) >= lot.cost(FEE) * 1.01 - 1e-9


def test_no_sell_signal_below_target():
    lots = replay([buy("USD", 1400, 100)], {"USD": USD})["USD"]
    sigs = strategy.evaluate(quote("USD", 1410), USD, lots, Strategy(min_profit_pct=1.0))
    assert not [s for s in sigs if s.side == "sell"]
    sigs = strategy.evaluate(quote("USD", 1415), USD, lots, Strategy(min_profit_pct=1.0))
    assert [s.side for s in sigs] == ["sell"] and sigs[0].profit > 0


def test_buy_signal_only_when_cheap():
    s = Strategy(buy_percentile=20)
    assert [x.side for x in strategy.evaluate(quote("USD", 1320), USD, [], s)] == ["buy"]
    assert strategy.evaluate(quote("USD", 1400), USD, [], s) == []


def test_additional_buy_only_below_average():
    s = Strategy(buy_percentile=20)
    buys = lambda lots, price: [x for x in strategy.evaluate(quote("USD", price), USD, lots, s) if x.side == "buy"]
    lots = replay([buy("USD", 1310, 100), buy("USD", 1330, 100)], {"USD": USD})["USD"]  # 평균 1320
    assert buys(lots, 1320) == []
    assert len(buys(lots, 1319)) == 1
    many = replay([buy("USD", 1330, 1)] * 10, {"USD": USD})["USD"]
    assert len(buys(many, 1320)) == 1  # 횟수 제한 없음


def test_alert_dedup_and_reset():
    alerts = {}
    q = {"USD": quote("USD", 1320)}
    assert len(strategy.run(CFG, q, {}, alerts, NOW)) == 1
    assert strategy.run(CFG, q, {}, alerts, NOW + timedelta(hours=1)) == []
    assert len(strategy.run(CFG, {"USD": quote("USD", 1310)}, {}, alerts, NOW + timedelta(hours=2))) == 1
    assert len(strategy.run(CFG, q, {}, alerts, NOW + timedelta(hours=15))) == 1
    strategy.run(CFG, {"USD": quote("USD", 1450)}, {}, alerts, NOW)
    assert "buy:USD" not in alerts


def test_bot_commands_use_toss_units():
    st = state.empty()
    assert "매수 기록 완료" in bot.handle("매수 JPY 880.5 100,000", st, CFG, {}, NOW)
    assert st["trades"][0]["rate"] == pytest.approx(8.805)
    assert "실현손익 1,500원" in bot.handle("/sell JPY 882 100000", st, CFG, {}, NOW)
    assert "보유량보다" in bot.handle("매도 JPY 900 1", st, CFG, {}, NOW)
    assert "마지막 기록 삭제" in bot.handle("취소", st, CFG, {}, NOW)
    assert len(st["trades"]) == 1
    assert "형식" in bot.handle("매수 XXX 1 1", st, CFG, {}, NOW)
    assert "숫자" in bot.handle("매수 USD nan 1", st, CFG, {}, NOW)
    assert "숫자" in bot.handle("매수 USD 1300 inf", st, CFG, {}, NOW)


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_KEY", Fernet.generate_key().decode())
    path = tmp_path / "state.enc"
    st = {**state.empty(), "trades": [buy("USD", 1300, 1)]}
    state.save(st, path)
    assert b"USD" not in path.read_bytes()
    assert state.load(path) == st
    size = path.stat().st_size
    state.save({**st, "trades": st["trades"] * 5}, path)
    assert path.stat().st_size == size  # 거래가 늘어도 파일 크기로 드러나지 않음


def test_sell_signal_on_any_profit():
    lots = replay([buy("USD", 1400, 100)], {"USD": USD})["USD"]
    assert strategy.evaluate(quote("USD", 1400), USD, lots, Strategy(min_profit_pct=0)) == []
    sigs = strategy.evaluate(quote("USD", 1400.1), USD, lots, Strategy(min_profit_pct=0))
    assert [s.side for s in sigs] == ["sell"] and sigs[0].profit > 0


def test_history_shows_average_of_holdings():
    st = state.empty()
    for cmd in ("매수 JPY 900 100000", "매수 JPY 880 100000", "매도 JPY 950 50000", "매수 USD 1300 10"):
        bot.handle(cmd, st, CFG, {}, NOW)
    text = bot.handle("기록", st, CFG, {}, NOW)
    # 선입선출로 900원 5만엔 + 880원 10만엔이 남음 → 평균 886.67
    assert "JPY(일본 엔) 150,000.00 / 평균 886.67 (2회 매수" in text
    assert "USD(미국 달러) 10.00 / 평균 1,300.00 (1회 매수, 원가 13,000원)" in text


def test_sell_all():
    st = state.empty()
    bot.handle("매수 JPY 900 100000", st, CFG, {}, NOW)
    bot.handle("매수 JPY 880 50000", st, CFG, {}, NOW)
    assert "환율을 가져오지 못했습니다" in bot.handle("매도 JPY 전량매도", st, CFG, {}, NOW)
    out = bot.handle("매도 JPY 전량매도", st, CFG, {"JPY": quote("JPY", 9.5)}, NOW)
    assert "150,000.00 @ 950.00" in out and "실현손익 85,000원" in out
    assert "보유 기록이 없습니다" in bot.handle("매도 JPY 전량", st, CFG, {}, NOW)
    bot.handle("매수 USD 1300 10", st, CFG, {}, NOW)
    assert "10.00 @ 1,310.00" in bot.handle("매도 USD 1310 전량", st, CFG, {}, NOW)
    assert "숫자" in bot.handle("매수 USD 1300 전량", st, CFG, {}, NOW)
