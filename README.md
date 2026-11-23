# Elpida

开源的国内期货多 agent 量化分析系统，第一个品种：**生猪（大商所 LH）**。

一群各管一摊的 agent（采集数据、技术面、基本面、消息面、期权定价……）把结果交到同一个数据库，
由加权 agent 汇总、判断 agent 拍板，网站（Dashboard）展示；后台有总管调度、迭代 agent 持续改进、学者 agent 积累知识。
整体设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

> 只做研究和分析，不构成投资建议。目前只给出数据和分析，不会自动下单。

## 现在能用的（第 1 阶段）

| 组成 | 做什么 |
|---|---|
| 总管 `elpida/orchestrator.py` | 按时间表叫醒 agent，记录每次运行，失败半小时后重试，网站上可以「立即运行」 |
| 行情数据 agent | 生猪、玉米、豆粕所有合约的日线（新浪公开接口），工作日 15:20、17:30 入库 |
| 期权定价 agent | 每张在交易的生猪合约对应的期权：美式二叉树理论价、隐含波动率、希腊值、波动率微笑 |
| Dashboard | 系统总览（所有 agent 状态，包括还没做的）、生猪行情（主力 K 线、期限结构、跨月价差、期货猪粮比、饲料价格）、期权定价（T 型报价、波动率微笑） |

## 部署到服务器

服务器上（Debian / Ubuntu，普通用户，不要 sudo）：

```bash
git clone https://github.com/cucumberling/Elpida.git ~/Elpida   # 已经有了就跳过
bash ~/Elpida/deploy/install.sh
```

装完会打印带访问钥匙的链接（以后用 `cat ~/elpida-data/owner_link` 查看）。之后每 15 分钟自动从 GitHub 拉新代码，
有变化就自动重启服务，不用再登录服务器。数据和钥匙都在 `~/elpida-data/`，永远不进 git。

常用命令：

```bash
systemctl status elpida-agents elpida-web --no-pager       # 服务状态
sudo journalctl -u elpida-agents -n 50 --no-pager            # 总管和 agent 的日志
sudo journalctl -u elpida-pull -n 20 --no-pager              # 自动更新的日志
cd ~/Elpida && ~/.local/share/elpida/venv/bin/python -m elpida status         # 所有 agent 的状态
cd ~/Elpida && ~/.local/share/elpida/venv/bin/python -m elpida run market_data  # 立刻拉一次行情
```

## 本地开发

```bash
pip install -r requirements.txt pytest
python -m pytest -q
ELPIDA_DATA=/tmp/elpida-dev python -m elpida run market_data   # 需要能访问新浪
ELPIDA_DATA=/tmp/elpida-dev python -m elpida web               # http://127.0.0.1:8780/?key=<web_token 文件里的钥匙>
```

## 目录

```
elpida/
  agents/      各个 agent（market_data 行情、pricing 期权定价……）
  skills/      可复用的技能（sina 数据源、continuous 主力连续、options 期权数学、dce 合约日历）
  web/         Dashboard 网站
  orchestrator.py  总管      registry.py  花名册（所有 agent 和技能）
  signal.py    分析类 agent 的统一「答卷」格式      db.py  数据库
deploy/        服务器安装（install.sh）和自动更新（update.sh）
docs/          设计文档
tests/
```
