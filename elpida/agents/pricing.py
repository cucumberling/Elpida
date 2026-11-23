"""定价 agent：给每张还在交易的生猪期货合约，计算对应期权的理论价、隐含波动率和希腊值。

  - 有市场报价（新浪期权链）时：用买卖中间价（没有就用最新价）反推隐含波动率，画出波动率微笑；
    同时给出「按历史波动率算的理论价」，两者对比，可以看出期权相对历史波动是贵还是便宜。
  - 拿不到市场报价时：在期货价上下自动生成一排行权价，只给理论价和希腊值。
大商所期权是美式，理论价用二叉树算（见 elpida/skills/options.py）。
"""
from datetime import datetime

import requests

from elpida import config, db
from elpida.agents.base import Agent, Schedule
from elpida.skills import continuous, dce, options, sina

KEY = "pricing:{product}"


def _mid(q):
    if not q:
        return None
    bid, ask, last = q.get("bid"), q.get("ask"), q.get("last")
    if bid and ask and bid > 0 and ask >= bid:
        return (bid + ask) / 2
    return last if last and last > 0 else None


def _r(x, n=2):
    return None if x is None else round(x, n)


def price_contract(code, bars, chain, today, r=None, steps=100):
    """一张期货合约对应的整条期权表。bars：该合约日线 DataFrame；chain：sina.parse_chain 的结果（可以为空）。"""
    r = config.RISK_FREE if r is None else r
    last = bars.iloc[-1]
    F = float(last["settle"]) if last.get("settle") and last["settle"] > 0 else float(last["close"])
    expiry = dce.option_expiry(code)
    days = (expiry - today).days
    if days <= 0:
        return None
    T = days / 365
    closes = list(bars["close"])
    hv20, hv60 = options.hist_vol(closes, 20), options.hist_vol(closes, 60)
    base_vol = hv20 or hv60 or 0.3
    if chain:
        strikes, source = [row["strike"] for row in chain], "market"
        quotes = {row["strike"]: row for row in chain}
    else:
        step = options.strike_step(F)
        atm = round(F / step) * step
        strikes, source, quotes = [atm + i * step for i in range(-10, 11)], "model", {}
    rows = []
    for K in strikes:
        row = {"strike": K}
        for kind in ("call", "put"):
            q = (quotes.get(K) or {}).get(kind)
            mkt = _mid(q)
            iv = options.implied_vol(mkt, F, K, T, r, kind, steps=steps) if mkt else None
            g = options.greeks(F, K, T, iv or base_vol, r, kind, steps=steps)
            theo = options.american(F, K, T, base_vol, r, kind, steps)
            row[kind] = {"market": _r(mkt), "theo": _r(theo), "iv": _r(iv, 4), "delta": _r(g["delta"], 3),
                         "gamma": _r(g["gamma"], 6), "vega": _r(g["vega"]), "theta": _r(g["theta"]),
                         "hold": (q or {}).get("hold")}
        rows.append(row)
    atm_row = min(rows, key=lambda x: abs(x["strike"] - F))
    ivs = [atm_row[k]["iv"] for k in ("call", "put") if atm_row[k]["iv"]]
    return {"contract": code, "future": F, "asof": str(bars.index[-1].date()), "expiry": expiry.isoformat(),
            "days": days, "hv20": _r(hv20, 4), "hv60": _r(hv60, 4), "vol_used": round(base_vol, 4),
            "atm_strike": atm_row["strike"], "atm_iv": _r(sum(ivs) / len(ivs), 4) if ivs else None,
            "source": source, "multiplier": config.PRODUCTS.get(code.rstrip("0123456789"), (0, 0, 1))[2],
            "rows": rows}


class Pricing(Agent):
    def __init__(self, product=config.FOCUS, chain_fetch=None, months_fetch=None):
        super().__init__(
            name="pricing", title="期权定价", layer="分析",
            desc="生猪各合约对应期权：理论价（美式二叉树）、隐含波动率、希腊值、波动率微笑",
            schedule=Schedule(times=("15:40",), every=30, window=("09:30", "15:00")))
        self.product = product
        self.chain_fetch = chain_fetch or sina.option_chain
        self.months_fetch = months_fetch or sina.option_months

    def run(self, con, log):
        rows = con.execute("SELECT * FROM bars WHERE product=? ORDER BY date", (self.product,)).fetchall()
        contracts = continuous.frames(rows)
        if not contracts:
            raise RuntimeError(f"数据库里还没有 {self.product} 的行情，先运行行情数据 agent")
        today = datetime.now(config.TZ).date()
        sess = requests.Session()
        chain_note = ""
        try:
            months = set(self.months_fetch(self.product, sess))
        except Exception as exc:
            months, chain_note = set(), f"取期权月份失败：{type(exc).__name__}"
        out, market = [], 0
        for code in continuous.active(contracts):
            chain = []
            month = code.lower()
            if month in months:
                try:
                    chain = self.chain_fetch(self.product, month, sess)
                except Exception as exc:
                    chain_note = f"取 {month} 期权链失败：{type(exc).__name__}"
            res = price_contract(code, contracts[code], chain, today)
            if res:
                out.append(res)
                market += res["source"] == "market"
        db.kv_set(con, KEY.format(product=self.product),
                  {"product": self.product, "computed_at": db.now(), "risk_free": config.RISK_FREE,
                   "note": chain_note, "contracts": out})
        msg = f"{len(out)} 张合约的期权表（{market} 张有市场报价）" + (f"；{chain_note}" if chain_note else "")
        log(msg)
        return msg
