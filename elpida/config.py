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
