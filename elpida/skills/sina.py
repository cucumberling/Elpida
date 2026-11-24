"""技能：从新浪财经公开接口取国内期货数据（逐合约日线、期权链）。

和 cucumberling/okx-quant 期货模块用的是同一套接口。只在服务器上能访问（需要能连 sina.com.cn）。
"""
import json
import re
from datetime import date

import requests

HEAD = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Referer": "https://finance.sina.com.cn/"}   # 新浪要求带来源页，否则拒绝
DAILY_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_X=/"
             "InnerFuturesNewService.getDailyKLine")
OPTION_PAGE = "https://stock.finance.sina.com.cn/futures/view/optionsDP.php/{prod}/{ex}"
OPTION_URL = "https://stock.finance.sina.com.cn/futures/api/openapi.php/OptionService.getOptionData"

# 有期权的品种 → 新浪期权接口里的 (product, exchange)
OPTION_CODES = {"LH": ("lh_o", "dce"), "C": ("c_o", "dce"), "M": ("m_o", "dce")}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None   # 去掉 NaN


def parse_daily(body):
    """解析日线接口返回的 JSONP 文本，返回 [{date, open, high, low, close, volume, hold, settle}]；没有数据返回 []。"""
    if "=(" not in body:
        return []
    raw = body.split("=(", 1)[1].rsplit(");", 1)[0].strip()
    if raw in ("", "null", "[]"):
        return []
    out = []
    for r in json.loads(raw) or []:
        bar = {"date": str(r.get("d", ""))[:10], "open": _num(r.get("o")), "high": _num(r.get("h")),
               "low": _num(r.get("l")), "close": _num(r.get("c")), "volume": _num(r.get("v")),
               "hold": _num(r.get("p")), "settle": _num(r.get("s"))}
        if bar["date"] and bar["close"] and bar["close"] > 0:
            out.append(bar)
    return out


def daily(contract, session=None):
    """一个合约的全部日线。合约不存在返回 []，网络错误抛异常。"""
    s = session or requests
    r = s.get(DAILY_URL, params={"symbol": contract, "type": date.today().strftime("%Y_%m_%d")},
              headers=HEAD, timeout=20)
    r.raise_for_status()
    return parse_daily(r.text)


def option_months(product, session=None):
    """某品种有期权的标的月份，例如 ['lh2601', 'lh2603']。"""
    if product not in OPTION_CODES:
        return []
    s = session or requests
    prod, ex = OPTION_CODES[product]
    r = s.get(OPTION_PAGE.format(prod=prod, ex=ex), headers=HEAD, timeout=15)
    r.raise_for_status()
    block = r.text.split('id="option_suffix"', 1)
    if len(block) < 2:
        return []
    return sorted(dict.fromkeys(m.lower() for m in re.findall(r'<li[^>]*data-value="([a-zA-Z]+\d{3,4})"', block[1][:5000])))


def parse_chain(data):
    """解析期权链接口的 result.data：每个行权价一行，带看涨、看跌的买价、卖价、最新价、持仓。"""
    ups, downs = data.get("up") or [], data.get("down") or []
    side = lambda x: {"bid": _num(x[1]), "last": _num(x[2]), "ask": _num(x[3]), "hold": _num(x[5])}
    rows = []
    for i, u in enumerate(ups):
        d = downs[i] if i < len(downs) else None
        strike = _num(u[7])
        if strike:
            rows.append({"strike": strike, "call": side(u), "put": side(d) if d else None})
    return sorted(rows, key=lambda r: r["strike"])


def option_chain(product, month, session=None):
    """期权链（month 例如 'lh2605'）。没有期权返回 []。"""
    if product not in OPTION_CODES:
        return []
    s = session or requests
    prod, ex = OPTION_CODES[product]
    r = s.get(OPTION_URL, params={"type": "futures", "product": prod, "exchange": ex, "pinzhong": month},
              headers=HEAD, timeout=15)
    r.raise_for_status()
    return parse_chain((r.json().get("result") or {}).get("data") or {})
