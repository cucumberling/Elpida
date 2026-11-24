from datetime import date

import numpy as np
import pandas as pd
import pytest
from conftest import make_bars

from elpida import db, orchestrator
from elpida.agents import fundamental as fu
from elpida.agents import judge as ju
from elpida.agents import market_data as md
from elpida.agents import technical as te
from elpida.agents import weighting as we
from elpida.signal import Signal


def trend_main(n=200, slope=0.004, start=14000):
    days = pd.bdate_range("2026-01-01", periods=n)
    c = start * np.exp(slope * np.arange(n) + np.random.default_rng(0).normal(0, 0.004, n))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "raw_close": c, "volume": 1.0,
                         "hold": np.linspace(1000, 2000, n), "contract": "LH2701"}, index=days)


def test_technical_scores_follow_trend():
    up = te.score_frame(trend_main(slope=0.004)).iloc[-1]
    down = te.score_frame(trend_main(slope=-0.004)).iloc[-1]
    assert up["score_1m"] > 0.5 and up["score_1w"] > 0.3
    assert down["score_1m"] < -0.5 and down["score_1w"] < -0.3
    flat = te.score_frame(trend_main(slope=0.0)).iloc[-1]
    assert abs(flat["score_1m"]) < 0.4
    for row in (up, down, flat):
        assert 0.1 <= row["conf_1m"] <= 0.9 and -1 <= row["score_1w"] <= 1


def test_technical_has_no_lookahead():
    full = te.score_frame(trend_main())
    cut = te.score_frame(trend_main().iloc[:150])
    pd.testing.assert_series_equal(full["score_1m"].iloc[:150], cut["score_1m"], check_names=False)


def test_hog_cost_and_profit_sign():
    cost = fu.hog_cost(2200, 3100)
    assert 11 < cost < 16                          # 行业常见的完全成本量级
    idx = pd.bdate_range("2026-01-01", periods=150)
    mk = lambda v: pd.DataFrame({"raw_close": v}, index=idx)
    pf = fu.profit_frame(mk(np.full(150, 11000.0)), mk(np.full(150, 2200.0)), mk(np.full(150, 3100.0)))
    s, detail = fu.profit_signal(pf)
    assert detail["profit_per_kg"] < 0 and s > 0.3           # 深度亏损 → 之后偏多
    pf = fu.profit_frame(mk(np.full(150, 20000.0)), mk(np.full(150, 2200.0)), mk(np.full(150, 3100.0)))
    assert fu.profit_signal(pf)[0] < -0.5                      # 暴利 → 之后偏空


def test_sow_signals():
    sows = {f"{y}-{m:02d}": 4000.0 for y in (2025, 2026) for m in range(1, 13)}
    out = fu.sow_signals(sows)
    # 最新一期 2026-12：6 个月看往前 3 个月那期（2026-09），3 个月看往前 6 个月那期（2026-06）
    assert out["6m"][1]["month"] == "2026-09" and out["3m"][1]["month"] == "2026-06"
    assert out["6m"][0] == pytest.approx(0)                     # 同比不变 → 中性
    sows["2026-09"] = 3600.0                                    # 母猪同比 −10% → 偏多
    assert fu.sow_signals(sows)["6m"][0] > 0.9
    assert "3m" in fu.sow_signals(sows) and fu.sow_signals({}) == {}


def test_combine_weights_and_dispersion():
    sig = lambda a, s, c: {"agent": a, "score": s, "confidence": c}
    score, conf, parts, disp = we.combine([sig("technical", 0.8, 0.6), sig("fundamental", 0.8, 0.4)],
                                          {"technical": 0.5, "fundamental": 0.5})
    assert score == pytest.approx(0.8) and disp == pytest.approx(0)
    score2, conf2, _, disp2 = we.combine([sig("technical", 0.8, 0.6), sig("fundamental", -0.8, 0.6)],
                                         {"technical": 0.5, "fundamental": 0.5})
    assert score2 == pytest.approx(0) and disp2 > 0.7 and conf2 < conf     # 分歧大 → 信心打折
    assert we.combine([sig("technical", 1, 0.5)], {"fundamental": 1}) is None


def test_decide_rules():
    h = lambda s1, s3, c=0.5: {"1m": {"score": s1, "confidence": c}, "3m": {"score": s3, "confidence": c}}
    assert ju.decide(h(0.6, 0.6), 0.6, 0.6, 0.2, 1)[:2] == ("做多", "较强")
    assert ju.decide(h(-0.3, -0.3), -0.3, -0.3, 0, 1)[:2] == ("做空", "轻仓试探")
    assert ju.decide(h(0.1, 0.1), 0.1, 0.1, 0, 1)[0] == "观望"
    assert ju.decide(h(0.6, 0.6, 0.2), 0.6, 0.6, 0, 1)[0] == "观望"           # 信心不够
    a, _, _, _, _, warn = ju.decide(h(0.5, 0.5), 0.7, -0.6, 0, 1)
    assert a == "观望" and "方向相反" in warn[0]                               # 技术面和基本面打架 → 否决
    assert ju.decide(h(0.6, 0.6), 0.6, 0.6, 0, 9)[0] == "观望"               # 数据过期
    a, _, _, _, _, warn = ju.decide(h(0.6, 0.6), 0.6, 0.6, -0.5, 1)
    assert a == "做多" and "等回调" in warn[0]


def test_pick_contract_rolls_before_individual_deadline():
    idx = pd.bdate_range("2026-10-01", periods=20)
    fr = lambda hold: pd.DataFrame({"close": 1.0, "hold": hold}, index=idx)
    frames = {"LH2701": fr(900.0), "LH2703": fr(500.0), "LH2705": fr(700.0)}
    assert ju.pick_contract(frames, "LH2701", date(2026, 11, 1)) == ("LH2701", None)
    code, note = ju.pick_contract(frames, "LH2701", date(2026, 12, 20))
    assert code == "LH2705" and "移仓" in note                                # 远月里持仓最大的


def test_full_pipeline_end_to_end(monkeypatch):
    """行情入库后，总管按流水线跑完技术面 → 基本面 → 加权 → 判断。"""
    with db.session() as con:
        for code, p, seed in (("LH2701", 13000, 1), ("LH2703", 13500, 2)):
            md.save_bars(con, "LH", code, make_bars(code, "2026-03-02", 190, p, drift=0.002, hold=1000 + seed * 500, seed=seed))
        md.save_bars(con, "C", "C2701", make_bars("C2701", "2026-03-02", 190, 2200, seed=3))
        md.save_bars(con, "M", "M2701", make_bars("M2701", "2026-03-02", 190, 3100, seed=4))
        db.kv_set(con, fu.MANUAL_KEY, {f"{y}-{m:02d}": 4000.0 - (y - 2025) * 200 for y in (2025, 2026) for m in range(1, 11)})
        agents = [a for a in __import__("elpida.registry", fromlist=["x"]).active_agents() if a.name not in ("market_data", "pricing")]
        orchestrator.run_with_followers(con, agents, "technical", "test", log=lambda m: None)
        orchestrator.run_with_followers(con, agents, "fundamental", "test", log=lambda m: None)
        runs = {r["agent"]: r["ok"] for r in con.execute("SELECT agent, ok FROM runs")}
        assert runs == {"technical": 1, "fundamental": 1, "weighting": 1, "judge": 1}, \
            [tuple(r) for r in con.execute("SELECT agent, message FROM runs")]
        w = db.kv_get(con, we.KEY.format(product="LH"))
        assert set(w["horizons"]) == {"1w", "1m", "3m", "6m"}
        j = db.kv_get(con, ju.KEY.format(product="LH"))
        assert j["action"] in ("做多", "做空", "观望") and j["plan"]["contract"] in ("LH2701", "LH2703")
        assert j["plan"]["risk_per_lot"] > 0
        assert len(db.kv_get(con, ju.LOG_KEY.format(product="LH"))) == 1
        f = db.kv_get(con, fu.KEY.format(product="LH"))
        assert f["profit_history"] and f["sows"]


def test_signal_validation():
    with pytest.raises(ValueError):
        Signal(agent="x", symbol="LH", asof="2026-01-01", horizon="2y", score=0, confidence=0.5)
    with pytest.raises(ValueError):
        Signal(agent="x", symbol="LH", asof="2026-01-01", horizon="1m", score=1.5, confidence=0.5)
