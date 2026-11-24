"""判断 agent：看综合分、分歧和风险，给出「做多 / 做空 / 观望」和理由。现在是规则版，第 3 阶段升级为 AI。

规则（以 1 个月为主、3 个月为辅，符合日线波段、不做短线的交易纪律）：
  - 主分数 = 1 个月综合分和 3 个月综合分按 0.7 : 0.3（再乘各自信心）加权
  - 主分数 ≥ +0.25 且信心 ≥ 0.35 → 做多；≤ −0.25 → 做空；否则观望。|主分数| ≥ 0.5 且信心 ≥ 0.45 算「较强」
  - 否决：技术面（1 个月）和基本面（3 个月）都很明确但方向相反 → 观望；行情数据过期 → 观望
  - 1 周分数和方向相反时不否决，但提示「等回调 / 反弹再进」
  - 合约：用主力；离个人客户平仓截止不到 20 天就换下一个持仓最大的远月（提示移仓）
  - 风控：止损 = 2 倍 ATR；给出每手风险，以及按「每笔最多亏总资金 0.5%」做 1 手至少要多少资金
  - 期权：隐含波动率明显低于历史波动率时，提示可以用买期权代替期货（最多亏权利金）
只是分析参考，不自动下单。
"""
from datetime import datetime

from elpida import config, db
from elpida.agents.base import Agent, Schedule
from elpida.agents.pricing import KEY as PRICING_KEY
from elpida.agents.weighting import KEY as WEIGHTING_KEY
from elpida.agents.weighting import latest_signals
from elpida.signal import Signal
from elpida.skills import continuous, dce
from elpida.skills import indicators as ind

KEY = "judge:{product}"
LOG_KEY = "judge_log:{product}"
ENTER, STRONG, MIN_CONF = 0.25, 0.5, 0.35
ROLL_DAYS = 20
RISK_PER_TRADE = 0.005


def pick_contract(frames, main_code, today):
    """主力合约；临近个人平仓截止就换成更远月里持仓最大的。返回 (合约, 提示或 None)。"""
    exit_by = dce.last_month_for_individuals(main_code)
    if (exit_by - today).days > ROLL_DAYS:
        return main_code, None
    later = [c for c in continuous.active(frames) if continuous.month_key(c) > continuous.month_key(main_code)]
    if not later:
        return main_code, f"{main_code} 离个人客户平仓截止（{exit_by}）不到 {ROLL_DAYS} 天，且没有更远的合约"
    nxt = max(later, key=lambda c: float(frames[c]["hold"].iloc[-1] or 0))
    return nxt, f"主力 {main_code} 离个人客户平仓截止（{exit_by}）不到 {ROLL_DAYS} 天，改用 {nxt}，已有持仓要移仓"


def decide(h, tech_1m, fund_3m, wk_1w, stale_days):
    """纯规则部分（方便测试）。h：{时间范围: {score, confidence}}。返回 (动作, 强度, 主分数, 信心, 理由列表, 警告列表)。"""
    reasons, warnings = [], []
    parts = [(h[k], w) for k, w in (("1m", 0.7), ("3m", 0.3)) if k in h]
    if not parts:
        return "观望", "", 0.0, 0.0, ["没有 1 个月或 3 个月的综合分"], warnings
    tot = sum(w * p["confidence"] for p, w in parts)
    score = sum(w * p["confidence"] * p["score"] for p, w in parts) / tot if tot > 0 else 0.0
    conf = sum(w * p["confidence"] for p, w in parts) / sum(w for _, w in parts)
    reasons.append(f"主分数 {score:+.2f}（1 个月为主、3 个月为辅），信心 {conf:.2f}")
    action = "做多" if score >= ENTER and conf >= MIN_CONF else "做空" if score <= -ENTER and conf >= MIN_CONF else "观望"
    if action == "观望":
        reasons.append(f"分数没到 ±{ENTER} 或信心不足 {MIN_CONF}，不出手")
    if stale_days > 5:
        warnings.append(f"最新行情是 {stale_days} 天前的，数据可能没更新")
        action = "观望"
    if tech_1m is not None and fund_3m is not None and abs(tech_1m) > 0.3 and abs(fund_3m) > 0.3 and tech_1m * fund_3m < 0:
        warnings.append(f"技术面（{tech_1m:+.2f}）和基本面（{fund_3m:+.2f}）方向相反，等方向一致再说")
        action = "观望"
    if action != "观望" and wk_1w is not None and wk_1w * score < 0 and abs(wk_1w) >= 0.4:
        warnings.append(f"1 周技术分 {wk_1w:+.2f} 与方向相反，短线逆风，等{'回调' if score > 0 else '反弹'}后再进更好")
    strength = "" if action == "观望" else "较强" if abs(score) >= STRONG and conf >= 0.45 else "轻仓试探"
    return action, strength, score, conf, reasons, warnings


class Judge(Agent):
    def __init__(self, product=config.FOCUS):
        super().__init__(name="judge", title="判断", layer="决策",
                         desc="看综合分、分歧和风险，给出做多 / 做空 / 观望、合约、止损和理由；有否决权（规则版）",
                         schedule=Schedule(), then=())
        self.product = product

    def run(self, con, log):
        w = db.kv_get(con, WEIGHTING_KEY.format(product=self.product))
        if not w or not w.get("horizons"):
            raise RuntimeError("还没有加权结果，先运行加权 agent")
        rows = con.execute("SELECT * FROM bars WHERE product=? ORDER BY date", (self.product,)).fetchall()
        frames = continuous.frames(rows)
        main = continuous.build_main(frames)
        today = datetime.now(config.TZ).date()
        last_day = main.index[-1].date()
        sigs = {(s["agent"], s["horizon"]): s for s in latest_signals(con, self.product)}
        get = lambda a, h: sigs[(a, h)]["score"] if (a, h) in sigs else None
        action, strength, score, conf, reasons, warnings = decide(
            w["horizons"], get("technical", "1m"), get("fundamental", "3m"), get("technical", "1w"),
            (today - last_day).days)
        for s in sorted(sigs.values(), key=lambda s: (s["agent"], s["horizon"])):
            if s["horizon"] in ("1m", "3m"):
                reasons.append(f"{s['agent']}（{s['horizon']}）{s['score']:+.2f}：{s['summary']}")
        code, roll_note = pick_contract(frames, main["contract"].iloc[-1], today)
        if roll_note:
            warnings.append(roll_note)
        bars = frames[code]
        price = float(bars["close"].iloc[-1])
        atr = float(ind.atr(bars, 20).iloc[-1]) if len(bars) > 21 else float(ind.atr(main, 20).iloc[-1])
        mult = config.PRODUCTS[self.product][2]
        risk_lot = 2 * atr * mult
        plan = {"contract": code, "price": round(price, 1), "atr": round(atr, 1), "multiplier": mult,
                "risk_per_lot": round(risk_lot), "lots_per_million": int(1_000_000 * RISK_PER_TRADE // risk_lot) if risk_lot else 0,
                "capital_per_lot": round(risk_lot / RISK_PER_TRADE, -4) if risk_lot else 0,
                "exit_by": dce.last_month_for_individuals(code).isoformat()}
        if action == "做多":
            plan["stop"] = round(price - 2 * atr, 1)
        elif action == "做空":
            plan["stop"] = round(price + 2 * atr, 1)
        option_note = self._option_note(con, code, action)
        decision = {"asof": last_day.isoformat(), "decided_at": db.now(), "action": action, "strength": strength,
                    "score": round(score, 3), "confidence": round(conf, 3), "reasons": reasons, "warnings": warnings,
                    "plan": plan, "option_note": option_note, "horizons": w["horizons"]}
        db.kv_set(con, KEY.format(product=self.product), decision)
        logs = [d for d in db.kv_get(con, LOG_KEY.format(product=self.product), []) if d["asof"] != decision["asof"]]
        logs.append({k: decision[k] for k in ("asof", "action", "strength", "score", "confidence")} | {"price": plan["price"], "contract": code})
        db.kv_set(con, LOG_KEY.format(product=self.product), logs[-250:])
        Signal(agent=self.name, symbol=self.product, asof=last_day.isoformat(), horizon="1m",
               score=round(max(-1.0, min(1.0, score)) if action != "观望" else 0.0, 3), confidence=round(conf, 3),
               summary=f"{action}{(' · ' + strength) if strength else ''}", detail={"contract": code}).save(con)
        msg = f"{last_day}：{action}{(' · ' + strength) if strength else ''}（{code}，主分数 {score:+.2f}）"
        log(msg)
        return msg

    def _option_note(self, con, code, action):
        pr = db.kv_get(con, PRICING_KEY.format(product=self.product)) or {}
        c = next((x for x in pr.get("contracts", []) if x["contract"] == code), None)
        if not c or not c.get("atm_iv") or not c.get("hv20"):
            return None
        iv, hv = c["atm_iv"], c["hv20"]
        txt = f"{code} 平值隐含波动率 {iv:.0%}，20 日历史波动率 {hv:.0%}。"
        if iv < hv * 0.9:
            txt += "期权相对便宜。"
            if action == "做多":
                txt += "可以考虑买看涨期权代替期货，最多亏掉权利金。"
            elif action == "做空":
                txt += "可以考虑买看跌期权代替期货，最多亏掉权利金。"
        elif iv > hv * 1.2:
            txt += "期权相对贵，不划算买期权；卖期权风险大，只适合有经验的人。"
        else:
            txt += "期权定价和历史波动差不多。"
        return txt
