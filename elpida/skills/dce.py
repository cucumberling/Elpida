"""技能：大商所合约日历。

期权最后交易日：标的期货合约交割月份前一个月的第 12 个交易日（大商所期权合约规则）。
这里按周一到周五算交易日，没有扣掉国庆、春节等节假日，所以遇到长假的月份会比真实日期早几天，
网站上会标「估算」。以后接入交易所日历后可以替换。
"""
from datetime import date, timedelta


def contract_month(code):
    """'LH2605' → (2026, 5)"""
    yymm = code[-4:]
    return 2000 + int(yymm[:2]), int(yymm[2:])


def nth_weekday(year, month, n):
    d = date(year, month, 1)
    count = 0
    while True:
        if d.weekday() < 5:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)


def option_expiry(code):
    """期权最后交易日（估算）。"""
    y, m = contract_month(code)
    y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return nth_weekday(y, m, 12)


def last_month_for_individuals(code):
    """个人客户必须在交割月之前平仓：返回交割月前一个月最后一天，判断 agent 以后用来提醒移仓。"""
    y, m = contract_month(code)
    return date(y, m, 1) - timedelta(days=1)
