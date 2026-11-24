"""全局配置：数据放在哪、看哪些品种、时区。

所有路径都可以用环境变量改（测试时用临时目录）：
  ELPIDA_DATA   数据目录，默认 ~/elpida-data（数据库、网站钥匙都在这里，永远不进 git）
  ELPIDA_PORT   网站监听端口，默认 8780（只监听 127.0.0.1，外面经 Caddy 的 HTTPS 访问）
"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(os.environ.get("ELPIDA_DATA", str(Path.home() / "elpida-data")))
DB_FILE = DATA / "elpida.db"
TOKEN_FILE = DATA / "web_token"          # 网站访问钥匙，由 deploy/install.sh 生成
DEPLOYED_FLAG = DATA / ".deployed"       # deploy/update.sh 拉到新代码后碰一下，systemd 看到就重启服务
PORT = int(os.environ.get("ELPIDA_PORT", "8780"))

TZ = ZoneInfo("Asia/Shanghai")           # 国内期货按北京时间

# 主角：生猪。玉米、豆粕是养猪的饲料成本，一起拉。
FOCUS = "LH"
PRODUCTS = {
    # 代码: (中文名, 交易所, 合约乘数 吨/手, 最小变动价位 元/吨, 从哪一年的合约开始下载)
    "LH": ("生猪", "DCE", 16, 5, 21),    # 2021 年 1 月上市
    "C": ("玉米", "DCE", 10, 1, 23),
    "M": ("豆粕", "DCE", 10, 1, 23),
}

RISK_FREE = float(os.environ.get("ELPIDA_RISK_FREE", "0.015"))   # 期权定价用的无风险利率（年化）

# 养殖成本估算（基本面 agent 用来从期货价推算养殖利润）。都是行业常见量级的假设，不是精确数字，可以按实际改。
HOG_COST = {
    "feed_per_kg": float(os.environ.get("ELPIDA_FCR", "3.0")),            # 全程料肉比：每长 1 公斤活猪（含母猪、仔猪分摊）约耗 3 公斤饲料
    "corn_share": 0.65, "meal_share": 0.20,                                # 饲料配方：玉米 65%、豆粕 20%
    "other_feed_price": 3000.0,                                            # 其余 15%（麸皮、预混料等）按 3000 元/吨
    "non_feed_per_kg": float(os.environ.get("ELPIDA_NONFEED", "5.5")),    # 非饲料成本（人工、防疫、折旧、财务等）元/公斤
}
