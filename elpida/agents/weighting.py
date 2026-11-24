"""加权 agent：把各分析 agent 最新的答卷，按时间范围分别合成综合分。

综合分 = Σ(权重 × 信心 × 分数) / Σ(权重 × 信心)
综合信心 = 参与的 agent 加权平均信心 ×（1 − 0.5 × 分歧度）；分歧度 = 各 agent 分数的加权标准差（0…1）
权重现在是固定的（DEFAULT_WEIGHTS），存在数据库里（weights:<品种>），第 4 阶段由迭代 agent 按各 agent 的历史准确率调整，
每次调整都会记下来。
"""
from datetime import date, timedelta

from elpida import config, db
from elpida.agents.base import Agent, Schedule
from elpida.signal import HORIZONS, Signal

KEY = "weighting:{product}"
WEIGHTS_KEY = "weights:{product}"
DEFAULT_WEIGHTS = {
    "1w": {"technical": 0.8, "fundamental": 0.2},
    "1m": {"technical": 0.55, "fundamental": 0.45},
    "3m": {"technical": 0.2, "fundamental": 0.8},
    "6m": {"fundamental": 1.0},
}
STALE_DAYS = 10          # 答卷依据的日期比最新行情早这么多天，就不用了


def latest_signals(con, symbol, exclude=("weighting", "judge")):
    """每个 (agent, 时间范围) 最新的一份答卷。"""
    rows = con.execute(
        "SELECT s.* FROM signals s JOIN (SELECT agent, horizon, MAX(id) AS id FROM signals WHERE symbol=? GROUP BY agent, horizon) t "
        "ON s.id = t.id ORDER BY s.agent, s.horizon", (symbol,)).fetchall()
    return [dict(r) for r in rows if r["agent"] not in exclude]


def combine(signals, weights):
    """signals：[{agent, score, confidence}]，weights：{agent: 权重}。返回 (分数, 信心, 各 agent 贡献)；没有可用答卷返回 None。"""
    used = [(s, weights.get(s["agent"], 0)) for s in signals if weights.get(s["agent"], 0) > 0]
    tot = sum(w * s["confidence"] for s, w in used)
    if not used or tot <= 0:
        return None
    score = sum(w * s["confidence"] * s["score"] for s, w in used) / tot
    wsum = sum(w for _, w in used)
    avg_conf = sum(w * s["confidence"] for s, w in used) / wsum
    disp = (sum(w * (s["score"] - score) ** 2 for s, w in used) / wsum) ** 0.5 if len(used) > 1 else 0.0
    conf = avg_conf * (1 - 0.5 * min(disp, 1.0))
    parts = [{"agent": s["agent"], "score": s["score"], "confidence": s["confidence"], "weight": w,
              "share": round(w * s["confidence"] / tot, 3)} for s, w in used]
    return score, conf, parts, disp


class Weighting(Agent):
    def __init__(self, product=config.FOCUS):
        super().__init__(name="weighting", title="加权", layer="汇总",
                         desc="把各分析 agent 的答卷按权重合成每个时间范围的综合分，并衡量分歧",
                         schedule=Schedule(), then=("judge",))
        self.product = product

    def run(self, con, log):
        weights = db.kv_get(con, WEIGHTS_KEY.format(product=self.product))
        if not weights:
            weights = DEFAULT_WEIGHTS
            db.kv_set(con, WEIGHTS_KEY.format(product=self.product), weights)
            db.kv_set(con, f"weights_log:{self.product}", [{"at": db.now(), "by": "default", "weights": weights}])
        sigs = latest_signals(con, self.product)
        if not sigs:
            raise RuntimeError("还没有任何分析 agent 的答卷")
        newest = max(date.fromisoformat(s["asof"]) for s in sigs)
        fresh = [s for s in sigs if date.fromisoformat(s["asof"]) >= newest - timedelta(days=STALE_DAYS)]
        out = {}
        for h in HORIZONS:
            res = combine([s for s in fresh if s["horizon"] == h], weights.get(h, {}))
            if res is None:
                continue
            score, conf, parts, disp = res
            out[h] = {"score": round(score, 3), "confidence": round(conf, 3), "dispersion": round(disp, 3), "parts": parts}
            Signal(agent=self.name, symbol=self.product, asof=newest.isoformat(), horizon=h, score=round(score, 3),
                   confidence=round(conf, 3), summary=f"{len(parts)} 个 agent 综合", detail=out[h]).save(con)
        db.kv_set(con, KEY.format(product=self.product), {
            "asof": newest.isoformat(), "horizons": out, "weights": weights,
            "signals": [{k: s[k] for k in ("agent", "horizon", "asof", "score", "confidence", "summary")} for s in fresh],
            "stale": [{"agent": s["agent"], "horizon": s["horizon"], "asof": s["asof"]} for s in sigs if s not in fresh]})
        msg = "，".join(f"{h} {v['score']:+.2f}（信心 {v['confidence']:.2f}）" for h, v in out.items())
        log(msg)
        return msg
