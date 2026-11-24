"""技能：把逐个合约的日线拼成「后复权主力连续」，以及期限结构、跨月价差。

主力 = 持仓量最大的合约，只往更远月切换。第 t 天收盘时发现别的合约持仓更大，第 t+1 天起换成它（不用未来数据）。
换月那天两张合约收盘价之差加到之前所有价格上（差值后复权），消除换月假跳空；最新一段就是真实合约价格。
（算法和 cucumberling/okx-quant 的 futures/continuous.py 相同。）
"""
import pandas as pd


def frames(rows):
    """数据库里的 bars 行 → {合约: 按日期排序的 DataFrame}"""
    if not rows:
        return {}
    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    out = {}
    for code, g in df.groupby("contract"):
        g = g[(g.close > 0) & (g.high >= g.low)].drop_duplicates("date").set_index("date").sort_index()
        if len(g) > 5:
            out[code] = g
    return out


def month_key(code):
    return int(code[-4:])


def build_main(contracts):
    """返回主力连续：open/high/low/close（已复权）、volume、hold、contract、raw_close、adj。"""
    if not contracts:
        return pd.DataFrame()
    codes = sorted(contracts, key=month_key)
    order = {c: i for i, c in enumerate(codes)}
    hold = pd.DataFrame({c: contracts[c]["hold"] for c in codes}).sort_index()
    close = pd.DataFrame({c: contracts[c]["close"] for c in codes}).sort_index()
    dates = hold.index
    cur, used, rolls = None, [], []
    for i, d in enumerate(dates):
        row = hold.loc[d]
        if cur is None or pd.isna(row[cur]):
            avail = row.dropna()
            if cur is not None:
                avail = avail[[c for c in avail.index if order[c] > order[cur]]]
            if avail.empty:
                used.append(None)
                continue
            new = avail.idxmax()
            if cur is not None and i and pd.notna(close.iloc[i - 1][new]) and pd.notna(close.iloc[i - 1][cur]):
                rolls.append((d, close.iloc[i - 1][new] - close.iloc[i - 1][cur]))
            cur = new
        used.append(cur)
        later = row[[c for c in codes if order[c] > order[cur]]].dropna()
        if not later.empty and later.max() > row[cur] and i + 1 < len(dates):
            new = later.idxmax()
            rolls.append((dates[i + 1], close.loc[d, new] - close.loc[d, cur]))
            cur = new
    rows = []
    for d, c in zip(dates, used):
        if c is None:
            continue
        r = contracts[c].loc[d]
        rows.append({"date": d, "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                     "volume": r.volume, "hold": r.hold, "contract": c})
    df = pd.DataFrame(rows).set_index("date")
    df["raw_close"] = df["close"]
    adj = pd.Series(0.0, index=df.index)
    for first_day, gap in rolls:
        adj[adj.index < first_day] += gap
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] + adj
    df["adj"] = adj
    return df


def curve(contracts, day=None):
    """期限结构：某一天（默认最近一天）每个合约的收盘价和持仓，按到期月份排序。"""
    if not contracts:
        return []
    day = day or max(g.index[-1] for g in contracts.values())
    out = []
    for code in sorted(contracts, key=month_key):
        g = contracts[code]
        if day in g.index:
            r = g.loc[day]
            out.append({"contract": code, "close": float(r.close), "hold": float(r.hold or 0)})
    return out


def active(contracts, within_days=10):
    """最近还在交易的合约（最后一根 K 线距最新交易日不超过 within_days 天）。"""
    if not contracts:
        return []
    last = max(g.index[-1] for g in contracts.values())
    return sorted([c for c, g in contracts.items() if (last - g.index[-1]).days <= within_days], key=month_key)


def spread(contracts, near, far):
    """两张合约收盘价差（近月 - 远月），只取两边都有数据的日子。"""
    a, b = contracts.get(near), contracts.get(far)
    if a is None or b is None:
        return pd.Series(dtype=float)
    return (a["close"] - b["close"]).dropna()
