from elpida.skills import sina


def test_parse_daily():
    body = 'var _X=([{"d":"2026-11-20","o":"12000","h":"12100","l":"11900","c":"12050","v":"5000","p":"80000","s":"12030"},' \
           '{"d":"2026-11-21","o":"0","h":"0","l":"0","c":"0","v":"0","p":"0","s":"0"}]);'
    bars = sina.parse_daily(body)
    assert bars == [{"date": "2026-11-20", "open": 12000.0, "high": 12100.0, "low": 11900.0, "close": 12050.0,
                     "volume": 5000.0, "hold": 80000.0, "settle": 12030.0}]   # 收盘价为 0 的坏数据被丢掉
    assert sina.parse_daily("var _X=(null);") == []
    assert sina.parse_daily("<html>error</html>") == []


def test_parse_chain():
    up = [["5", "820", "830", "840", "3", "100", "1.2", "13000", "lh2605C13000"]]
    down = [["2", "300", "305", "310", "4", "80", "-0.5", "lh2605P13000"]]
    rows = sina.parse_chain({"up": up, "down": down})
    assert rows == [{"strike": 13000.0, "call": {"bid": 820.0, "last": 830.0, "ask": 840.0, "hold": 100.0},
                     "put": {"bid": 300.0, "last": 305.0, "ask": 310.0, "hold": 80.0}}]
