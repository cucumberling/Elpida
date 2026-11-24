"""技术面 agent：看生猪主力连续的日线，给出 1 周、1 个月两个时间范围的看多 / 看空分数。

四个部分，每个都换算成 −1…+1：
  趋势     20 日均线在 60 日均线之上多少（用 ATR 衡量），管 1 个月
  突破     收盘价在过去 20 天最高、最低价之间的位置；创新高 = +1，创新低 = −1，管 1 周
  动量     10 天涨跌幅相对正常波动有多大，两个时间范围都用
  超买超卖 RSI 高于 75 扣分、低于 25 加分（防止追在最高点），管 1 周
持仓量配合（价涨仓增、价跌仓增，说明新资金在推动）时提高信心，反之降低。

score_frame() 对每一天都算一遍（只用当天及以前的数据），既给今天出答卷，也方便以后回测、对答案。
"""
import math

import numpy as np
import pandas as pd

from elpida import config, db
from elpida.agents.base import Agent, Schedule
from elpida.signal import Signal
from elpida.skills import continuous
from elpida.skills import indicators as ind

KEY = "technical:{product}"


def score_frame(main):
    """main：主力连续 DataFrame。返回每天的各部分分数、两个时间范围的总分和信心。"""
    c = main["close"]
    a = ind.atr(main, 20)
    ma20, ma60 = ind.ma(c, 20), ind.ma(c, 60)
    hi20, lo20 = ind.donchian(main, 20)
    r = ind.rsi(c, 14)
    out = pd.DataFrame(index=main.index)
    out["close"] = c
    out["atr"] = a
    out["ma20"], out["ma60"] = ma20, ma60
    out["high20"], out["low20"] = hi20, lo20
    out["rsi"] = r
    out["trend"] = ind.clip((ma20 - ma60) / (3 * a))
    rng = (hi20 - lo20).replace(0, np.nan)
    out["breakout"] = ind.clip(2 * (c - lo20) / rng - 1)
    move = c - c.shift(10)
    out["momentum"] = ind.clip(move / (a * math.sqrt(10)) / 1.5)
    out["rsi_adj"] = np.where(r > 75, -(r - 75) / 25, np.where(r < 25, (25 - r) / 25, 0.0))
    d_oi = main["hold"] - main["hold"].shift(5)
    out["oi_confirm"] = np.where(d_oi > 0, 1.0, np.where(d_oi < 0, -1.0, 0.0))   # 增仓 +1，减仓 −1
    # 主力换月那天持仓量会跳，不能算「增仓 / 减仓」
    rolled = main["contract"] != main["contract"].shift(5) if "contract" in main else False
    out.loc[rolled, "oi_confirm"] = 0.0
    out["score_1w"] = ind.clip(0.4 * out["breakout"] + 0.4 * out["momentum"] + 0.2 * out["rsi_adj"])
    out["score_1m"] = ind.clip(0.7 * out["trend"] + 0.3 * out["momentum"])
    for h, parts in (("1w", ("breakout", "momentum")), ("1m", ("trend", "momentum"))):
        s = out[f"score_{h}"]
        agree = (np.sign(out[parts[0]]) == np.sign(out[parts[1]])).astype(float)   # 两个主要部分方向一致
        conf = 0.35 + 0.15 * agree + 0.25 * s.abs()
        conf = conf + 0.1 * out["oi_confirm"] * (s.abs() > 0.2)                    # 增仓推动更可信，减仓（平仓）推动不可信
        out[f"conf_{h}"] = ind.clip(conf, 0.1, 0.9)
    return out


def _summary(row, h):
    s = row[f"score_{h}"]
    side = "偏多" if s > 0.15 else "偏空" if s < -0.15 else "中性"
    bits = []
    if h == "1m":
        bits.append(f"20 日均线{'高于' if row['ma20'] > row['ma60'] else '低于'} 60 日均线")
    else:
        pos = row["breakout"]
        bits.append("创 20 日新高" if pos >= 1 else "创 20 日新低" if pos <= -1 else f"位于 20 日区间{'上' if pos > 0 else '下'}半部")
    bits.append(f"10 日{'上涨' if row['momentum'] > 0 else '下跌'}")
    if row["rsi"] > 75:
        bits.append(f"RSI {row['rsi']:.0f} 超买")
    elif row["rsi"] < 25:
        bits.append(f"RSI {row['rsi']:.0f} 超卖")
    if row["oi_confirm"] > 0:
        bits.append("近 5 天增仓")
    elif row["oi_confirm"] < 0:
        bits.append("近 5 天减仓")
    return f"{side}：" + "，".join(bits)


class Technical(Agent):
    def __init__(self, product=config.FOCUS):
        super().__init__(name="technical", title="技术面", layer="分析",
                         desc="主力连续日线：均线趋势、20 日突破、动量、RSI、持仓量配合 → 1 周 / 1 个月打分",
                         schedule=Schedule(), then=("weighting",))
        self.product = product

    def run(self, con, log):
        rows = con.execute("SELECT * FROM bars WHERE product=? ORDER BY date", (self.product,)).fetchall()
        main = continuous.build_main(continuous.frames(rows))
        if len(main) < 80:
            raise RuntimeError(f"{self.product} 主力连续只有 {len(main)} 天，至少要 80 天才能算技术指标")
        sf = score_frame(main).dropna(subset=["score_1m", "score_1w", "atr"])
        last = sf.iloc[-1]
        asof = str(sf.index[-1].date())
        for h in ("1w", "1m"):
            Signal(agent=self.name, symbol=self.product, asof=asof, horizon=h,
                   score=round(float(last[f"score_{h}"]), 3), confidence=round(float(last[f"conf_{h}"]), 3),
                   summary=_summary(last, h),
                   detail={k: round(float(last[k]), 3) for k in
                           ("trend", "breakout", "momentum", "rsi_adj", "rsi", "oi_confirm", "atr", "ma20", "ma60",
                            "high20", "low20", "close")}).save(con)
        hist = sf.tail(500)
        db.kv_set(con, KEY.format(product=self.product), {
            "asof": asof, "main": main["contract"].iloc[-1],
            "history": [{"time": str(d.date()), "close": round(float(r.close), 1), "s1w": round(float(r.score_1w), 3),
                         "s1m": round(float(r.score_1m), 3)} for d, r in hist.iterrows()]})
        msg = f"{asof}：1 周 {last['score_1w']:+.2f}，1 个月 {last['score_1m']:+.2f}"
        log(msg)
        return msg
