"""数据库（SQLite，一个文件）。所有 agent 之间不直接说话，只通过这里交换数据。

表：
  bars     每个合约每天一根日线
  signals  分析类 agent 交的「答卷」（见 elpida/signal.py），以后加权 agent 从这里读
  runs     每个 agent 每次运行的记录：什么时候、成功没有、说明
  kv       各 agent 的最新结果快照（JSON），例如定价 agent 算好的期权表
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from elpida import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  contract TEXT NOT NULL, product TEXT NOT NULL, date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL, hold REAL, settle REAL,
  PRIMARY KEY (contract, date)
);
CREATE INDEX IF NOT EXISTS bars_product ON bars (product, date);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL, symbol TEXT NOT NULL, asof TEXT NOT NULL, horizon TEXT NOT NULL,
  score REAL NOT NULL, confidence REAL NOT NULL, summary TEXT, detail TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS signals_lookup ON signals (symbol, agent, asof);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  ok INTEGER, message TEXT, trigger TEXT
);
CREATE INDEX IF NOT EXISTS runs_agent ON runs (agent, started_at);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def now():
    return datetime.now(config.TZ).isoformat(timespec="seconds")


def connect(path=None):
    path = path or config.DB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")     # 网站读、agent 写，可以同时进行
    con.executescript(SCHEMA)
    return con


@contextmanager
def session(path=None):
    con = connect(path)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def kv_get(con, key, default=None):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def kv_set(con, key, value):
    con.execute("INSERT INTO kv (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), now()))
