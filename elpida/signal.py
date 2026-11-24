"""「答卷」：所有分析类 agent（技术面、基本面、消息面……）交给加权 agent 的统一格式。

格式统一，加权 agent 才能把不同 agent 的意见放在一起算；迭代 agent 也才能逐个对答案、统计谁准。
"""
import json
from dataclasses import asdict, dataclass, field

from elpida import db

HORIZONS = ("1d", "1w", "1m", "3m", "6m")   # 这个判断管多长时间：1 天、1 周、1 个月、3 个月、6 个月


@dataclass
class Signal:
    agent: str              # 哪个 agent 交的
    symbol: str             # 品种或合约，例如 LH 或 LH2605
    asof: str               # 依据的是哪一天收盘时已经能看到的信息（YYYY-MM-DD），回测时防止偷看未来
    horizon: str            # 见 HORIZONS
    score: float            # -1（强烈看空）… 0（中性）… +1（强烈看多）
    confidence: float       # 0 … 1，这个判断有多大把握
    summary: str = ""       # 一句话理由，给人看
    detail: dict = field(default_factory=dict)   # 证据和中间数字，给迭代 agent 复盘用

    def __post_init__(self):
        if self.horizon not in HORIZONS:
            raise ValueError(f"horizon 只能是 {HORIZONS}，收到 {self.horizon!r}")
        if not -1 <= self.score <= 1:
            raise ValueError(f"score 必须在 -1 到 1 之间，收到 {self.score}")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence 必须在 0 到 1 之间，收到 {self.confidence}")

    def save(self, con):
        con.execute("INSERT INTO signals (agent, symbol, asof, horizon, score, confidence, summary, detail, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (self.agent, self.symbol, self.asof, self.horizon, self.score, self.confidence,
                     self.summary, json.dumps(self.detail, ensure_ascii=False), db.now()))

    def to_dict(self):
        return asdict(self)
