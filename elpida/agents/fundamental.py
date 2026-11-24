"""基本面 agent：生猪的供需和周期。

三个依据：
  1. 养殖利润（期货隐含）：生猪价 − 养殖成本。成本按玉米、豆粕期货价和固定假设估算（config.HOG_COST）。
     猪周期的逻辑：长期深度亏损 → 养殖户淘汰母猪、减产 → 几个月后供应变少、猪价回升；暴利则相反。
     所以利润越差，对 3–6 个月后越偏多。
  2. 能繁母猪存栏（手工录入，网站上填；农业农村部每月公布）：母猪变化约 10 个月后变成出栏量变化。
     某一期同比减少 → 约 10 个月后出栏少 → 偏多。6 个月看最新一期往前 3 个月那期，3 个月看往前 6 个月那期。
  3. 季节性：过去几年同一时间段之后一个月的平均涨跌。生猪期货 2021 年才上市，年份少，信心给得很低。
"""
import math
from datetime import date

import numpy as np
import pandas as pd

from elpida import config, db
from elpida.agents.base import Agent, Schedule
from elpida.signal import Signal
from elpida.skills import continuous
from elpida.skills import indicators as ind

KEY = "fundamental:{product}"
MANUAL_KEY = "manual:sows"          # {"2026-10": 3980.0, ...}（万头）


def hog_cost(corn, meal, cost=None):
    """每公斤活猪的完全成本（元/公斤）。corn、meal 是 元/吨。"""
    k = cost or config.HOG_COST
    other = 1 - k["corn_share"] - k["meal_share"]
    feed_price = (k["corn_share"] * corn + k["meal_share"] * meal + other * k["other_feed_price"]) / 1000   # 元/公斤饲料
    return k["feed_per_kg"] * feed_price + k["non_feed_per_kg"]


def profit_frame(hog_main, corn_main, meal_main, cost=None):
    """每天的期货隐含养殖利润。用各品种主力合约的真实价格（未复权）。"""
    df = pd.DataFrame({"hog": hog_main["raw_close"], "corn": corn_main["raw_close"], "meal": meal_main["raw_close"]}).ffill().dropna()
    df["cost"] = hog_cost(df["corn"], df["meal"], cost)
    df["price"] = df["hog"] / 1000                      # 元/吨 → 元/公斤
    df["profit"] = df["price"] - df["cost"]
    df["margin"] = df["profit"] / df["cost"]
    return df


def profit_signal(pf):
    m_now = float(pf["margin"].iloc[-1])
    m_avg = float(pf["margin"].tail(120).mean())        # 近半年平均：持续亏损才会真正去产能
    s = float(ind.clip(-(0.5 * m_now + 0.5 * m_avg) / 0.25))
    state = "亏损" if m_now < -0.03 else "盈利" if m_now > 0.03 else "盈亏平衡附近"
    return s, {"margin": round(m_now, 4), "margin_120d": round(m_avg, 4), "profit_per_kg": round(float(pf["profit"].iloc[-1]), 2),
               "cost_per_kg": round(float(pf["cost"].iloc[-1]), 2), "price_per_kg": round(float(pf["price"].iloc[-1]), 2),
               "text": f"期货隐含养殖{state}（每公斤 {pf['profit'].iloc[-1]:+.2f} 元，利润率 {m_now:+.0%}，近半年平均 {m_avg:+.0%}）"}


def _month_add(ym, k):
    y, m = map(int, ym.split("-"))
    m += k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def sow_signals(sows):
    """sows：{'YYYY-MM': 万头}。返回 {horizon: (score, detail)}，数据不够的时间范围不返回。"""
    if not sows:
        return {}
    months = sorted(sows)
    out = {}
    latest = months[-1]
    # 母猪约 10 个月后变成出栏。官方数据大约晚 1 个月公布，所以「H 个月后的供应」对应最新一期往前 (9 − H) 个月那期
    for h, lag in (("6m", 3), ("3m", 6)):
        m = _month_add(latest, -lag)
        prev = _month_add(m, -12)
        if m in sows and prev in sows and sows[prev] > 0:
            yoy = sows[m] / sows[prev] - 1
            out[h] = (float(ind.clip(-yoy / 0.08)), {"month": m, "yoy": round(yoy, 4),
                      "text": f"{m} 能繁母猪 {sows[m]:.0f} 万头，同比 {yoy:+.1%}（母猪{'少' if yoy < 0 else '多'} → 之后出栏{'少' if yoy < 0 else '多'}）"})
    return out


def seasonality(hog_main, asof, days=30):
    """过去每年从「今天这个日子」起 days 天的涨跌（主力连续，复权价）。"""
    c = hog_main["close"]
    rets = []
    for y in range(c.index[0].year, asof.year):
        try:
            start = pd.Timestamp(date(y, asof.month, min(asof.day, 28)))
        except ValueError:
            continue
        a = c[c.index >= start]
        if a.empty or (a.index[0] - start).days > 7:
            continue
        b = c[c.index >= a.index[0] + pd.Timedelta(days=days)]
        if b.empty:
            continue
        rets.append((y, math.log(b.iloc[0] / a.iloc[0])))
    return rets


class Fundamental(Agent):
    def __init__(self, product=config.FOCUS):
        super().__init__(name="fundamental", title="基本面", layer="分析",
                         desc="养殖利润（期货隐含）、能繁母猪存栏（网站录入）、季节性 → 1 个月 / 3 个月 / 6 个月打分",
                         schedule=Schedule(), then=("weighting",))
        self.product = product

    def _main(self, con, product):
        rows = con.execute("SELECT * FROM bars WHERE product=? ORDER BY date", (product,)).fetchall()
        return continuous.build_main(continuous.frames(rows))

    def run(self, con, log):
        hog, corn, meal = self._main(con, self.product), self._main(con, "C"), self._main(con, "M")
        if hog.empty or corn.empty or meal.empty:
            raise RuntimeError("生猪、玉米、豆粕的行情还不全，先运行行情数据 agent")
        asof_ts = hog.index[-1]
        asof = str(asof_ts.date())
        pf = profit_frame(hog, corn, meal)
        p_score, p_detail = profit_signal(pf)
        sows = db.kv_get(con, MANUAL_KEY, {})
        sw = sow_signals(sows)
        seas = seasonality(hog, asof_ts.date())
        notes = []
        saved = {}
        # 1 个月：季节性（信心很低）
        if seas:
            avg = float(np.mean([r for _, r in seas]))
            hit = float(np.mean([np.sign(r) == np.sign(avg) for _, r in seas]))
            s = float(ind.clip(avg / 0.08))
            conf = round(min(0.3, 0.05 + 0.04 * len(seas)) * hit, 3)
            text = f"过去 {len(seas)} 年同期之后一个月平均{'上涨' if avg > 0 else '下跌'} {abs(avg):.1%}，{hit:.0%} 的年份同方向"
            saved["1m"] = (s, max(conf, 0.05), text, {"seasonality": [{"year": y, "ret": round(r, 4)} for y, r in seas]})
        else:
            notes.append("历史太短，没有季节性")
        # 3 个月、6 个月：利润 + 母猪
        for h, p_w, p_conf in (("3m", 0.5, 0.3), ("6m", 1.0, 0.35)):
            parts = [(p_score * p_w, p_conf, p_detail["text"])]
            if h in sw:
                parts.append((sw[h][0], 0.5, sw[h][1]["text"]))
            wsum = sum(c for _, c, _ in parts)
            s = sum(sc * c for sc, c, _ in parts) / wsum
            conf = min(0.7, max(c for _, c, _ in parts) + 0.1 * (len(parts) - 1))
            saved[h] = (float(ind.clip(s)), conf, "；".join(t for _, _, t in parts),
                        {"profit": p_detail, "sows": sw.get(h, (None, None))[1]})
        if not sw:
            notes.append("没有能繁母猪数据（到网站「今日建议」页录入），3 / 6 个月只看养殖利润")
        for h, (s, conf, text, detail) in saved.items():
            side = "偏多" if s > 0.15 else "偏空" if s < -0.15 else "中性"
            Signal(agent=self.name, symbol=self.product, asof=asof, horizon=h, score=round(s, 3),
                   confidence=round(conf, 3), summary=f"{side}：{text}", detail=detail).save(con)
        tail = pf.tail(750)
        db.kv_set(con, KEY.format(product=self.product), {
            "asof": asof, "notes": notes, "profit": p_detail, "cost_assumptions": config.HOG_COST,
            "profit_history": [{"time": str(d.date()), "profit": round(float(r.profit), 3), "cost": round(float(r.cost), 3),
                                "price": round(float(r.price), 3)} for d, r in tail.iterrows()],
            "seasonality": [{"year": y, "ret": round(r, 4)} for y, r in seas],
            "sows": [{"month": m, "value": sows[m]} for m in sorted(sows)]})
        msg = f"{asof}：" + "，".join(f"{h} {v[0]:+.2f}" for h, v in saved.items()) + (f"（{'；'.join(notes)}）" if notes else "")
        log(msg)
        return msg
