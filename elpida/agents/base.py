"""所有 agent 的共同骨架。

一个 agent = 名字 + 说明 + 什么时候跑（Schedule）+ run()。
run() 拿到一个数据库连接，自己读需要的数据、把结果写回数据库，最后返回一句话说明这次做了什么。
跑失败就抛异常，总管会记下来并在网站上显示。
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class Schedule:
    times: tuple = ()             # 每天固定的时刻（北京时间），例如 ("15:20", "17:00")
    every: int = 0                # 另外：在 window 时间段内每隔多少分钟跑一次，0 表示不用
    window: tuple = ("09:00", "15:00")
    weekdays: tuple = (0, 1, 2, 3, 4)   # 周一到周五

    def last_slot(self, now):
        """now 之前（含）最近一个应该运行的时刻；一个都没有返回 None。只往回看 7 天。"""
        best = None
        for back in range(8):
            day = (now - timedelta(days=back)).date()
            if day.weekday() not in self.weekdays:
                continue
            slots = [_at(now, day, t) for t in self.times]
            if self.every:
                t, end = _at(now, day, self.window[0]), _at(now, day, self.window[1])
                while t <= end:
                    slots.append(t)
                    t += timedelta(minutes=self.every)
            slots = [s for s in slots if s <= now]
            if slots:
                cand = max(slots)
                best = cand if best is None or cand > best else best
            if best is not None and best.date() == day:
                break
        return best

    def describe(self):
        parts = []
        if self.times:
            parts.append("每天 " + "、".join(self.times))
        if self.every:
            parts.append(f"{self.window[0]}–{self.window[1]} 每 {self.every} 分钟")
        days = "工作日" if tuple(self.weekdays) == (0, 1, 2, 3, 4) else "每天" if len(self.weekdays) == 7 else "指定日"
        return days + " " + "；".join(parts) if parts else "手动"


def _at(now, day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=now.tzinfo)


@dataclass
class Agent:
    name: str
    title: str
    layer: str
    desc: str
    schedule: Schedule = field(default_factory=Schedule)
    then: tuple = ()              # 本 agent 成功后，紧接着要跑的 agent

    def run(self, con, log):      # pragma: no cover - 子类实现
        raise NotImplementedError
