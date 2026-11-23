"""行情数据 agent：每天收盘后把生猪、玉米、豆粕所有合约的日线拉进数据库。

第一次运行会下载上市以来的全部合约（几百个请求，几分钟）；之后只更新还在交易的合约和新上市的远月合约。
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import requests

from elpida import config, db
from elpida.agents.base import Agent, Schedule
from elpida.skills import sina

MISSING_KEY = "market_data:missing"     # 查过不存在的合约 → 上次查的时间戳


def candidates(products, today):
    out = []
    for p, (_, _, _, _, first_year) in products.items():
        for yy in range(first_year, today.year % 100 + 2):
            for mm in range(1, 13):
                out.append((p, f"{p}{yy:02d}{mm:02d}"))
    return out


def todo(con, products, today, now_ts):
    """需要（重新）下载的合约：还在交易的、以前没查过的、可能刚上市的远月。"""
    last = {r["contract"]: r["last"] for r in
            con.execute("SELECT contract, MAX(date) AS last FROM bars GROUP BY contract")}
    missing = db.kv_get(con, MISSING_KEY, {})
    cur = (today.year % 100) * 100 + today.month
    fresh = (today - timedelta(days=15)).isoformat()
    out = []
    for product, code in candidates(products, today):
        if code in last:
            if last[code] >= fresh:
                out.append((product, code))          # 还在交易：每天更新
            continue
        seen = missing.get(code)
        # 没有数据的合约：过去的月份查过一次就不再查；当月及以后（可能还没上市）每 7 天再查一次
        if seen is None or (int(code[-4:]) >= cur and now_ts - seen > 7 * 86400):
            out.append((product, code))
    return out


def save_bars(con, product, code, bars):
    con.executemany(
        "INSERT INTO bars (contract, product, date, open, high, low, close, volume, hold, settle) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(contract, date) DO UPDATE SET "
        "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
        "volume=excluded.volume, hold=excluded.hold, settle=excluded.settle",
        [(code, product, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"], b["hold"], b["settle"])
         for b in bars])


class MarketData(Agent):
    def __init__(self, fetch=None, products=None):
        super().__init__(
            name="market_data", title="行情数据", layer="采集",
            desc="生猪、玉米、豆粕所有合约的日线（新浪），收盘后入库",
            schedule=Schedule(times=("15:20", "17:30")), then=("pricing",))
        self.fetch = fetch or sina.daily
        self.products = products or config.PRODUCTS

    def run(self, con, log):
        today = datetime.now(config.TZ).date()
        now_ts = time.time()
        jobs = todo(con, self.products, today, now_ts)
        missing = db.kv_get(con, MISSING_KEY, {})
        sess = requests.Session()

        def one(job):
            product, code = job
            for attempt in range(3):
                try:
                    return product, code, self.fetch(code, sess), None
                except Exception as exc:   # 网络抖动：重试
                    err = f"{type(exc).__name__}: {str(exc)[:150]}"
                    time.sleep(1 + attempt)
            return product, code, None, err

        got = errors = 0
        last_err = ""
        with ThreadPoolExecutor(6) as ex:
            for product, code, bars, err in ex.map(one, jobs):
                if err:
                    errors += 1
                    last_err = err
                    continue
                if bars and len(bars) > 5:
                    save_bars(con, product, code, bars)
                    missing.pop(code, None)
                    got += 1
                else:
                    missing[code] = now_ts
        db.kv_set(con, MISSING_KEY, missing)
        con.commit()
        msg = f"检查 {len(jobs)} 个合约，更新 {got} 个，失败 {errors} 个"
        if jobs and errors == len(jobs):
            raise RuntimeError(msg + f"（全部失败，网络可能连不上新浪。最后一个错误：{last_err}）")
        log(msg)
        return msg


def latest_trade_day(con, product):
    row = con.execute("SELECT MAX(date) AS d FROM bars WHERE product=?", (product,)).fetchone()
    return date.fromisoformat(row["d"]) if row and row["d"] else None
