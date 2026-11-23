"""总管的花名册：系统里所有的 agent 和技能（skill）。

已经做好的 agent 在 ACTIVE 里，会被总管按时间表调度；还没做的列在 PLANNED 里，网站上显示为「待开发」，
这样随时能看到整个系统的全貌和进度。加一个新 agent：写好放进 elpida/agents/，再加到 ACTIVE。
"""
from elpida.agents.market_data import MarketData
from elpida.agents.pricing import Pricing


def active_agents():
    return [MarketData(), Pricing()]


# (名字, 中文名, 层, 说明, 计划在第几阶段做)
PLANNED = [
    ("news", "消息采集", "采集", "农业农村部公告和周度价格、收储抛储、猪瘟疫情、牧原温氏等月度销售简报、行业资讯", 3),
    ("fundamental_data", "基本面数据", "采集", "能繁母猪存栏、仔猪价、出栏均重、冻品库存、屠宰量、现货价", 2),
    ("technical", "技术面", "分析", "日线波段趋势（均线方向 + 突破 + ATR 止损）、跨月价差、持仓量变化", 2),
    ("fundamental", "基本面", "分析", "生猪供需表：能繁母猪推算未来出栏、养殖利润、猪粮比、季节性、基差", 2),
    ("sentiment", "消息面", "分析", "判断每条消息对猪价是利多还是利空、影响多大（AI）", 3),
    ("weighting", "加权", "汇总", "把各分析 agent 的答卷按权重合成综合分，权重按历史准确率调整", 2),
    ("judge", "判断", "决策", "看综合分、分歧和风险，给出买 / 卖 / 观望和理由；交割月前强制移仓，有否决权", 2),
    ("evolution", "迭代", "后台", "每天对答案、统计各 agent 成绩、调权重、回测新想法并提 PR 等你批准", 4),
    ("scholar", "学者", "后台", "积累猪周期研究、农产品期货交易方法、行为金融，整理成规则卡片和策略假设", 5),
]

# 技能：可复用的能力，agent 按需调用。(名字, 说明, 用到它的 agent)
SKILLS = [
    ("sina", "新浪财经公开接口：国内期货逐合约日线、期权链", ("market_data", "pricing")),
    ("continuous", "主力连续合约（后复权）、期限结构、跨月价差", ("pricing", "dashboard")),
    ("options", "美式期权二叉树、Black-76、隐含波动率、希腊值、历史波动率", ("pricing",)),
    ("dce", "大商所合约日历：期权到期日、个人客户移仓截止日（估算）", ("pricing",)),
]
