import pytest
from conftest import make_bars

from elpida import db
from elpida.agents import market_data as md
from elpida.agents import pricing
from elpida.skills import continuous, options


def _store(con, code, n=80, price=13000):
    md.save_bars(con, "LH", code, make_bars(code, "2026-07-01", n, price))


def test_price_contract_recovers_market_vol():
    from datetime import date
    with db.session() as con:
        _store(con, "LH2705")
        frames = continuous.frames(con.execute("SELECT * FROM bars").fetchall())
    bars = frames["LH2705"]
    F = float(bars.iloc[-1]["settle"])
    today = date(2026, 11, 23)
    T = (date(2027, 4, 16) - today).days / 365
    chain = []
    for K in (12000, 13000, 14000):
        c = options.american(F, K, T, 0.32, 0.015, "call", 100)
        p = options.american(F, K, T, 0.32, 0.015, "put", 100)
        chain.append({"strike": K, "call": {"bid": c - 1, "ask": c + 1, "last": c, "hold": 5},
                      "put": {"bid": p - 1, "ask": p + 1, "last": p, "hold": 5}})
    res = pricing.price_contract("LH2705", bars, chain, today, r=0.015)
    assert res["source"] == "market" and res["expiry"] == "2027-04-16" and res["multiplier"] == 16
    for row in res["rows"]:
        assert row["call"]["iv"] == pytest.approx(0.32, abs=0.005)
        assert row["put"]["iv"] == pytest.approx(0.32, abs=0.005)
    assert res["atm_iv"] == pytest.approx(0.32, abs=0.005)


def test_model_only_when_no_chain_and_expired_contracts_skipped():
    from datetime import date
    with db.session() as con:
        _store(con, "LH2705")
        frames = continuous.frames(con.execute("SELECT * FROM bars").fetchall())
    res = pricing.price_contract("LH2705", frames["LH2705"], [], date(2026, 11, 23))
    assert res["source"] == "model" and len(res["rows"]) == 21
    assert all(r["call"]["iv"] is None and r["call"]["theo"] > 0 for r in res["rows"])
    assert pricing.price_contract("LH2705", frames["LH2705"], [], date(2027, 5, 1)) is None


def test_agent_survives_option_source_failure():
    def boom(*a):
        raise ConnectionError("blocked")

    with db.session() as con:
        _store(con, "LH2705")
        _store(con, "LH2709", price=14000)
        msg = pricing.Pricing(chain_fetch=boom, months_fetch=boom).run(con, log=lambda m: None)
        out = db.kv_get(con, pricing.KEY.format(product="LH"))
    assert "2 张合约" in msg and "失败" in out["note"]
    assert [c["contract"] for c in out["contracts"]] == ["LH2705", "LH2709"]


def test_agent_requires_data():
    with db.session() as con, pytest.raises(RuntimeError):
        pricing.Pricing(chain_fetch=lambda *a: [], months_fetch=lambda *a: []).run(con, log=lambda m: None)
