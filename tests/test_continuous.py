import pandas as pd

from elpida.skills import continuous


def _frame(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def test_roll_is_back_adjusted_without_lookahead():
    days = [f"2026-01-0{i}" for i in range(1, 9)]
    near = _frame([{"date": d, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1, "hold": 100 - i * 10}
                   for i, d in enumerate(days)])
    far = _frame([{"date": d, "open": 110, "high": 110, "low": 110, "close": 110, "volume": 1, "hold": 30 + i * 10}
                  for i, d in enumerate(days)])
    main = continuous.build_main({"LH2601": near, "LH2603": far})
    # 第 4 天（i=3）收盘时远月持仓 60 > 近月 70？不；i=4 时远月 70 > 近月 60 → 第 6 天起换成远月
    assert list(main["contract"]) == ["LH2601"] * 5 + ["LH2603"] * 3
    assert main["close"].iloc[-1] == 110                   # 最新一段是真实价格
    assert (main["close"] == 110).all()                    # 换月前的价格平移了 +10，没有假跳空
    assert main["raw_close"].iloc[0] == 100


def test_curve_active_spread():
    days = pd.bdate_range("2026-01-01", periods=10)
    a = _frame([{"date": d, "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1, "hold": 5} for d in days])
    b = _frame([{"date": d, "open": 1, "high": 1, "low": 1, "close": 120, "volume": 1, "hold": 6} for d in days])
    old = _frame([{"date": d, "open": 1, "high": 1, "low": 1, "close": 90, "volume": 1, "hold": 1}
                  for d in pd.bdate_range("2025-06-01", periods=10)])
    frames = {"LH2603": a, "LH2605": b, "LH2509": old}
    assert continuous.active(frames) == ["LH2603", "LH2605"]
    assert [c["contract"] for c in continuous.curve(frames)] == ["LH2603", "LH2605"]
    assert (continuous.spread(frames, "LH2603", "LH2605") == -20).all()
