"""命令行入口。

  python -m elpida loop            总管常驻运行（systemd 服务 elpida-agents）
  python -m elpida web             网站（systemd 服务 elpida-web）
  python -m elpida run <agent>     立刻跑一个 agent（以及它的下游），例如 run market_data
  python -m elpida status          看所有 agent 的状态
"""
import sys

from elpida import db, orchestrator, registry


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "loop":
        orchestrator.loop()
    elif cmd == "web":
        from elpida.web import app
        app.serve()
    elif cmd == "run" and len(argv) == 2:
        agents = registry.active_agents()
        if argv[1] not in {a.name for a in agents}:
            print("没有这个 agent，可选：", ", ".join(a.name for a in agents))
            return 2
        with db.session() as con:
            orchestrator.run_with_followers(con, agents, argv[1], "cli")
    elif cmd == "status":
        with db.session() as con:
            for s in orchestrator.status(con):
                if s["state"] == "planned":
                    print(f"  [待开发·第{s['phase']}阶段] {s['title']}")
                    continue
                last = s["last"]
                res = "从未运行" if not last else f"{last['started_at']} {'成功' if last['ok'] else '失败'}：{last['message']}"
                print(f"  [{s['layer']}] {s['title']}（{s['name']}，{s['schedule']}）：{res}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
