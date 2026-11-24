"""技能：技术指标（输入 DataFrame：open/high/low/close/hold，按日期排序；输出同索引的 Series）。"""
import numpy as np
import pandas as pd


def ma(s, n):
    return s.rolling(n, min_periods=n).mean()


def atr(df, n=20):
    """平均真实波幅：一天正常能波动多少元/吨。"""
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(up.notna())


def donchian(df, n=20):
    """过去 n 天（不含今天）的最高价、最低价。"""
    return df["high"].shift(1).rolling(n, min_periods=n).max(), df["low"].shift(1).rolling(n, min_periods=n).min()


def clip(x, lo=-1.0, hi=1.0):
    return np.clip(x, lo, hi)
