from datetime import date

from conftest import make_bars

from elpida import db
from elpida.agents import market_data as md

PRODUCTS = {"LH": ("生猪", "DCE", 16, 5, 26)}


def test_run_saves_bars_and_remembers_missing(monkeypatch):
    calls = []

    def fetch(code, sess):
        calls.append(code)
        return make_bars(code, "2026-10-01", 30, 12000) if code in ("LH2701", "LH2703") else []

    agent = md.MarketData(fetch=fetch, products=PRODUCTS)
    with db.session() as con:
        msg = agent.run(con, log=lambda m: None)
        assert "更新 2 个" in msg
        assert con.execute("SELECT COUNT(DISTINCT contract) FROM bars").fetchone()[0] == 2
        missing = db.kv_get(con, md.MISSING_KEY)
        assert "LH2601" in missing and "LH2701" not in missing
        now_ts = __import__("time").time()
        # 第二次：过去月份查过不存在的不再查；还在交易的（最后一根 K 线在 15 天内）继续更新
        jobs = {c for _, c in md.todo(con, PRODUCTS, date(2026, 11, 23), now_ts)}
        assert "LH2601" not in jobs and {"LH2701", "LH2703"} <= jobs
        # 一个月以后这两张合约已经不更新了：不再查
        jobs = {c for _, c in md.todo(con, PRODUCTS, date(2026, 12, 31), now_ts)}
        assert "LH2701" not in jobs and "LH2703" not in jobs


def test_all_failures_raise():
    def fetch(code, sess):
        raise ConnectionError("blocked")

    agent = md.MarketData(fetch=fetch, products={"LH": ("生猪", "DCE", 16, 5, 27)})
    monkey_sleep = __import__("time").sleep
    import time
    time.sleep = lambda s: None
    try:
        with db.session() as con:
            try:
                agent.run(con, log=lambda m: None)
                raise AssertionError("应该失败")
            except RuntimeError as exc:
                assert "全部失败" in str(exc)
    finally:
        time.sleep = monkey_sleep
