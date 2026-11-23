from datetime import datetime, timedelta

from elpida import config, db, orchestrator
from elpida.agents.base import Agent, Schedule


class Fake(Agent):
    def __init__(self, name, then=(), fail=False, schedule=None):
        super().__init__(name=name, title=name, layer="测试", desc="", schedule=schedule or Schedule(times=("15:20",)), then=then)
        self.fail, self.calls = fail, 0

    def run(self, con, log):
        self.calls += 1
        if self.fail:
            raise RuntimeError("坏了")
        return "好了"


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=config.TZ)


def test_schedule_last_slot():
    s = Schedule(times=("15:20", "17:30"))
    assert s.last_slot(at("2026-11-23T16:00")) == at("2026-11-23T15:20")      # 周一
    assert s.last_slot(at("2026-11-23T10:00")) == at("2026-11-20T17:30")      # 周一上午 → 上周五
    assert s.last_slot(at("2026-11-22T12:00")) == at("2026-11-20T17:30")      # 周日 → 周五
    w = Schedule(every=30, window=("09:30", "15:00"))
    assert w.last_slot(at("2026-11-23T10:45")) == at("2026-11-23T10:30")


def test_due_runs_once_per_slot_and_retries_failures(monkeypatch):
    clock = {"now": at("2026-11-23T16:00")}
    monkeypatch.setattr(db, "now", lambda: clock["now"].isoformat(timespec="seconds"))
    ok, bad = Fake("ok"), Fake("bad", fail=True)
    with db.session() as con:
        orchestrator.tick(con, [ok, bad], now=clock["now"], log=lambda m: None)
        assert (ok.calls, bad.calls) == (1, 1)                 # 从来没跑过：马上跑
        clock["now"] += timedelta(minutes=10)
        orchestrator.tick(con, [ok, bad], now=clock["now"], log=lambda m: None)
        assert (ok.calls, bad.calls) == (1, 1)                 # 同一轮不重复跑，失败的 30 分钟内不重试
        clock["now"] += timedelta(minutes=25)
        orchestrator.tick(con, [ok, bad], now=clock["now"], log=lambda m: None)
        assert (ok.calls, bad.calls) == (1, 2)                 # 失败的过了半小时重试
        clock["now"] = at("2026-11-24T15:21")
        orchestrator.tick(con, [ok, bad], now=clock["now"], log=lambda m: None)
        assert ok.calls == 2                                   # 第二天到点再跑
        runs = con.execute("SELECT agent, ok, message FROM runs ORDER BY id").fetchall()
        assert [tuple(r) for r in runs][:2] == [("ok", 1, "好了"), ("bad", 0, "RuntimeError: 坏了")]


def test_followers_and_manual_requests():
    b = Fake("b")
    a = Fake("a", then=("b",))
    now = datetime.now(config.TZ)
    with db.session() as con:
        orchestrator.tick(con, [a, b], now=now, log=lambda m: None)
        assert (a.calls, b.calls) == (1, 1)                    # a 成功后马上跑 b（b 不会再因为「从没跑过」多跑一次）
        orchestrator.request_run(con, "b")
        orchestrator.tick(con, [a, b], now=now, log=lambda m: None)
        assert b.calls == 2
        assert con.execute("SELECT trigger FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0] == "manual"


def test_failed_agent_does_not_trigger_followers():
    b = Fake("b", schedule=Schedule())
    a = Fake("a", then=("b",), fail=True)
    with db.session() as con:
        orchestrator.run_with_followers(con, [a, b], "a", "cli", log=lambda m: None)
    assert b.calls == 0


def test_interrupted_runs_are_closed():
    with db.session() as con:
        con.execute("INSERT INTO runs (agent, started_at) VALUES ('x', '2026-01-01T00:00:00+08:00')")
        orchestrator.mark_interrupted(con)
        r = con.execute("SELECT ok, message FROM runs").fetchone()
        assert (r["ok"], r["message"]) == (0, "进程重启，运行被中断")


def test_status_includes_planned():
    with db.session() as con:
        st = orchestrator.status(con, [Fake("a")])
    assert st[0]["state"] == "active"
    assert {"judge", "weighting", "scholar", "evolution"} <= {s["name"] for s in st if s["state"] == "planned"}
