import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from elpida import config  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    """每个测试用自己的临时数据目录，绝不碰 ~/elpida-data。"""
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "elpida.db")
    monkeypatch.setattr(config, "TOKEN_FILE", tmp_path / "web_token")
    return tmp_path


def make_bars(code, start, n, price, drift=0.0, hold=1000.0, seed=0):
    """造一段日线（工作日），返回可以直接存库的 bar 列表。"""
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n)
    closes = price * np.exp(np.cumsum(rng.normal(drift, 0.02, n)))
    return [{"date": d.strftime("%Y-%m-%d"), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
             "volume": 100.0, "hold": hold, "settle": c} for d, c in zip(days, closes)]
