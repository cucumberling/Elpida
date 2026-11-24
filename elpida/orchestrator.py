"""总管：按时间表叫醒各个 agent，记下每次运行的结果，失败了过一会儿重试，网站上点「立即运行」也由它执行。

作为 systemd 服务 elpida-agents 一直运行（python -m elpida loop）。
"""
import time
import traceback
from datetime import datetime, timedelta

from elpida import config, db, registry

RETRY_AFTER = timedelta(minutes=30)
REQUESTS_KEY = "orchestrator:requests"
HEARTBEAT_KEY = "orchestrator:heartbeat"


def _parse(ts):
    return datetime.fromisoformat(ts) if ts else None


def last_runs(con, name):
    """(最近一次运行, 最近一次成功)"""
    last = con.execute("SELECT * FROM runs WHERE agent=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    ok = con.execute("SELECT * FROM runs WHERE agent=? AND ok=1 ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    return last, ok


def is_due(con, agent, now):
    last, ok = last_runs(con, agent.name)
    if last is None:
        return True                                     # 从来没跑过：马上跑一次
    if last["finished_at"] is None and now - _parse(last["started_at"]) < timedelta(hours=2):
        return False                                    # 正在跑（或刚刚异常退出，2 小时后再说）
    slot = agent.schedule.last_slot(now)
    if slot is None:
        return False
    started = _parse(last["started_at"])
    if started < slot:
        return True                                     # 到点了，这一轮还没跑
    if not last["ok"] and (ok is None or _parse(ok["started_at"]) < slot) and now - started >= RETRY_AFTER:
        return True                                     # 这一轮跑失败了：半小时后重试
    return False


def run_agent(con, agent, trigger="schedule", log=print):
    cur = con.execute("INSERT INTO runs (agent, started_at, trigger) VALUES (?,?,?)", (agent.name, db.now(), trigger))
    run_id = cur.lastrowid
    con.commit()
    try:
        msg = agent.run(con, lambda m: log(f"[{agent.name}] {m}"))
        ok = True
    except Exception as exc:
        con.rollback()
        msg = f"{type(exc).__name__}: {exc}"
        log(f"[{agent.name}] 失败：{msg}\n{traceback.format_exc()}")
        ok = False
    con.execute("UPDATE runs SET finished_at=?, ok=?, message=? WHERE id=?", (db.now(), int(ok), (msg or "")[:2000], run_id))
    con.commit()
    return ok


def run_with_followers(con, agents, name, trigger, log=print):
    """跑一个 agent；成功后接着跑它的下游（例如行情更新完马上重算期权）。"""
    by_name = {a.name: a for a in agents}
    queue, done = [(name, trigger)], set()
    while queue:
        n, why = queue.pop(0)
        if n in done or n not in by_name:
            continue
        done.add(n)
        if run_agent(con, by_name[n], why, log):
            queue.extend((f, f"after:{n}") for f in by_name[n].then)


def request_run(con, name):
    """网站上点「立即运行」：先记下来，由总管进程去执行（网站本身不跑 agent）。"""
    reqs = db.kv_get(con, REQUESTS_KEY, [])
    if name not in reqs:
        reqs.append(name)
    db.kv_set(con, REQUESTS_KEY, reqs)


def tick(con, agents, now=None, log=print):
    now = now or datetime.now(config.TZ)
    db.kv_set(con, HEARTBEAT_KEY, {"at": now.isoformat(timespec="seconds")})
    con.commit()
    reqs = db.kv_get(con, REQUESTS_KEY, [])
    if reqs:
        db.kv_set(con, REQUESTS_KEY, [])
        con.commit()
        for name in reqs:
            run_with_followers(con, agents, name, "manual", log)
    for agent in agents:
        if is_due(con, agent, now):
            run_with_followers(con, agents, agent.name, "schedule", log)


def mark_interrupted(con):
    """上次进程被重启（例如部署了新代码）时没跑完的记录，标成失败，免得一直显示「运行中」、挡住重跑。"""
    con.execute("UPDATE runs SET finished_at=?, ok=0, message='进程重启，运行被中断' WHERE finished_at IS NULL", (db.now(),))


def loop(interval=30, log=print):
    agents = registry.active_agents()
    log(f"总管启动：{', '.join(a.name for a in agents)}")
    with db.session() as con:
        mark_interrupted(con)
    while True:
        try:
            with db.session() as con:
                tick(con, agents, log=log)
        except Exception:
            log("总管本轮出错：\n" + traceback.format_exc())
        time.sleep(interval)


def status(con, agents=None):
    """给网站和命令行用：每个 agent（含待开发的）的状态。"""
    agents = agents if agents is not None else registry.active_agents()
    out = []
    for a in agents:
        last, ok = last_runs(con, a.name)
        out.append({"name": a.name, "title": a.title, "layer": a.layer, "desc": a.desc, "state": "active",
                    "schedule": a.schedule.describe(), "then": list(a.then),
                    "last": dict(last) if last else None, "last_ok": dict(ok) if ok else None})
    for name, title, layer, desc, phase in registry.PLANNED:
        out.append({"name": name, "title": title, "layer": layer, "desc": desc, "state": "planned", "phase": phase})
    return out
