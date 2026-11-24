import math

import pytest

from elpida.skills import options as o


def test_black76_put_call_parity():
    F, K, T, s, r = 15000, 14000, 0.5, 0.3, 0.02
    c, p = o.black76(F, K, T, s, r, "call"), o.black76(F, K, T, s, r, "put")
    assert c - p == pytest.approx(math.exp(-r * T) * (F - K), rel=1e-9)


def test_american_close_to_european_and_never_below():
    F, T, s, r = 15000, 0.4, 0.3, 0.015
    for K in (13000, 15000, 17000):
        for kind in ("call", "put"):
            eu = o.black76(F, K, T, s, r, kind)
            am = o.american(F, K, T, s, r, kind, steps=300)
            assert am >= eu - 0.5                       # 美式 ≥ 欧式（允许二叉树离散误差）
            assert am == pytest.approx(eu, rel=0.02)    # 利率低时提前行权价值很小


def test_american_deep_itm_has_early_exercise_premium():
    # 深度实值、利率高：提前行权有价值，美式明显高于欧式
    F, K, T, s, r = 20000, 10000, 1.0, 0.2, 0.08
    assert o.american(F, K, T, s, r, "call") > o.black76(F, K, T, s, r, "call") + 100
    assert o.american(F, K, T, s, r, "call") >= F - K - 1e-6   # 不低于立刻行权的价值


def test_implied_vol_round_trip():
    F, T, r = 14500, 0.3, 0.015
    for K in (13000, 14500, 16000):
        for kind in ("call", "put"):
            price = o.american(F, K, T, 0.27, r, kind, steps=100)
            assert o.implied_vol(price, F, K, T, r, kind) == pytest.approx(0.27, abs=2e-3)


def test_implied_vol_rejects_impossible_prices():
    assert o.implied_vol(100, 15000, 14000, 0.3, 0.015, "call") is None   # 低于内在价值 1000
    assert o.implied_vol(None, 15000, 14000, 0.3, 0.015, "call") is None
    assert o.implied_vol(20000, 15000, 14000, 0.3, 0.015, "call") is None  # 高得离谱


def test_greeks_signs():
    g_c = o.greeks(15000, 15000, 0.3, 0.3, 0.015, "call", steps=100)
    g_p = o.greeks(15000, 15000, 0.3, 0.3, 0.015, "put", steps=100)
    assert 0.4 < g_c["delta"] < 0.6 and -0.6 < g_p["delta"] < -0.4
    assert g_c["gamma"] > 0 and g_c["vega"] > 0 and g_c["theta"] < 0


def test_hist_vol():
    closes = [100 * math.exp(0.01 * (i % 2)) for i in range(30)]   # 每天 ±1%
    hv = o.hist_vol(closes, 20)
    assert hv == pytest.approx(0.01 * math.sqrt(244) * math.sqrt(21 / 20) * 1.0, rel=0.1)
    assert o.hist_vol(closes[:10], 20) is None
