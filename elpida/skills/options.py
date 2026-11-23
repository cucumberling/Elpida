"""技能：商品期货期权定价。

大商所的期货期权（包括生猪期权）是美式期权：到期前任何一个交易日都可以行权。所以：
  - 理论价用二叉树（Cox-Ross-Rubinstein）算美式期权，标的是期货（期货不占用资金，没有持有收益，漂移为 0）；
  - Black-76（欧式）公式只作为参考和速算，美式价格总是 ≥ 欧式价格；
  - 隐含波动率：给定市场价，反推出让二叉树价格等于市场价的波动率；
  - 希腊值（Delta、Gamma、Vega、Theta）用数值微小变动重新计算得到。

单位：价格都是 元/吨；波动率是年化（0.25 = 25%）；T 是年（按日历日 / 365）。
"""
import math

import numpy as np

STEPS = 150


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black76(F, K, T, sigma, r, kind):
    """欧式期货期权价格。kind: 'call' 或 'put'。"""
    disc = math.exp(-r * T)
    intrinsic = max(F - K, 0.0) if kind == "call" else max(K - F, 0.0)
    if T <= 0 or sigma <= 0:
        return disc * intrinsic
    sd = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if kind == "call":
        return disc * (F * _ncdf(d1) - K * _ncdf(d2))
    return disc * (K * _ncdf(-d2) - F * _ncdf(-d1))


def american(F, K, T, sigma, r, kind, steps=STEPS):
    """美式期货期权价格（二叉树）。"""
    intrinsic = max(F - K, 0.0) if kind == "call" else max(K - F, 0.0)
    if T <= 0 or sigma <= 0:
        return intrinsic
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1 / u
    p = (1 - d) / (u - d)                        # 期货的风险中性概率（漂移为 0）
    disc = math.exp(-r * dt)
    prices = F * u ** np.arange(steps, -1, -1) * d ** np.arange(0, steps + 1)
    sign = 1.0 if kind == "call" else -1.0
    values = np.maximum(sign * (prices - K), 0.0)
    for _ in range(steps):
        prices = prices[:-1] * d                 # 往前一步各节点的期货价
        cont = disc * (p * values[:-1] + (1 - p) * values[1:])
        values = np.maximum(cont, sign * (prices - K))   # 美式：每个节点比较「继续持有」和「立刻行权」
    return float(values[0])


def implied_vol(price, F, K, T, r, kind, lo=0.01, hi=3.0, tol=1e-4, steps=100):
    """隐含波动率（二分法）。市场价低于内在价值或高于上限对应的价格时返回 None。"""
    if price is None or price <= 0 or T <= 0:
        return None
    intrinsic = max(F - K, 0.0) if kind == "call" else max(K - F, 0.0)
    if price < intrinsic - 1e-9:
        return None
    f_lo = american(F, K, T, lo, r, kind, steps) - price
    f_hi = american(F, K, T, hi, r, kind, steps) - price
    if f_lo > 0 or f_hi < 0:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        f_mid = american(F, K, T, mid, r, kind, steps) - price
        if abs(f_mid) < tol * max(1.0, price * 1e-3) or hi - lo < 1e-5:
            return mid
        if f_mid > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def greeks(F, K, T, sigma, r, kind, steps=STEPS):
    """美式期权的价格和希腊值。
    delta：期货每涨 1 元/吨，期权涨多少元/吨；gamma：delta 的变化速度；
    vega：波动率每升 1 个百分点，期权涨多少元/吨；theta：每过 1 天，期权跌多少元/吨（通常为负）。"""
    price = american(F, K, T, sigma, r, kind, steps)
    h = F * 0.01
    up = american(F + h, K, T, sigma, r, kind, steps)
    dn = american(F - h, K, T, sigma, r, kind, steps)
    vega = american(F, K, T, sigma + 0.01, r, kind, steps) - price
    day = 1 / 365
    theta = (american(F, K, T - day, sigma, r, kind, steps) - price) if T > day else -price
    return {"price": price, "delta": (up - dn) / (2 * h), "gamma": (up - 2 * price + dn) / (h * h),
            "vega": vega, "theta": theta}


def hist_vol(closes, window):
    """历史波动率：最近 window 个交易日对数收益率的标准差 × √244（年化，按一年 244 个交易日）。"""
    c = np.asarray([x for x in closes if x and x > 0], dtype=float)
    if len(c) < window + 1:
        return None
    rets = np.diff(np.log(c[-(window + 1):]))
    return float(rets.std(ddof=1) * math.sqrt(244))


def strike_step(F):
    """自己生成一排行权价时用的间距（元/吨）。按大商所常见的分档估计（≤10000 为 100，10000–20000 为 200，
    >20000 为 400），没有逐条核对生猪期权的合约规则。只在拿不到市场期权链时使用，有市场数据就用市场的行权价。"""
    return 100 if F <= 10000 else 200 if F <= 20000 else 400
