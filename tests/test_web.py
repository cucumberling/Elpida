import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from conftest import make_bars

from elpida import config, db
from elpida.agents import market_data as md
from elpida.web import app


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@pytest.fixture
def server(tmp_data):
    config.TOKEN_FILE.write_text("secret-key\n")
    with db.session() as con:
        md.save_bars(con, "LH", "LH2701", make_bars("LH2701", "2026-06-01", 120, 12000, hold=500))
        md.save_bars(con, "LH", "LH2703", make_bars("LH2703", "2026-06-01", 120, 12500, hold=900, seed=1))
        md.save_bars(con, "C", "C2701", make_bars("C2701", "2026-06-01", 120, 2200))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url, cookie=None, data=None):
    req = urllib.request.Request(url, data=data, headers={"Cookie": cookie} if cookie else {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(req)
        return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_requires_key(server):
    assert get(server + "/")[0] == 401
    assert get(server + "/api/status")[0] == 401
    assert get(server + "/?key=wrong")[0] == 401
    assert get(server + "/", cookie="elpida_key=wrong")[0] == 401
    assert get(server + "/api/run", data=b'{"agent":"market_data"}')[0] == 401


def test_key_link_sets_cookie_and_redirects(server):
    code, headers, _ = get(server + "/?key=secret-key")
    assert code == 302 and headers["Location"] == "/"
    assert "elpida_key=secret-key" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]


def test_apis(server):
    ck = "elpida_key=secret-key"
    code, _, body = get(server + "/", cookie=ck)
    assert code == 200 and "Elpida" in body.decode()
    st = json.loads(get(server + "/api/status", cookie=ck)[2])
    assert {a["name"] for a in st["agents"]} >= {"market_data", "pricing", "judge"}
    assert st["data"][0]["contracts"] == 2
    hog = json.loads(get(server + "/api/hog", cookie=ck)[2])
    assert hog["main"] == "LH2703" and len(hog["bars"]) == 120
    assert [c["contract"] for c in hog["curve"]] == ["LH2701", "LH2703"]
    assert hog["ratio"] and 3 < hog["ratio"][-1]["value"] < 10
    assert json.loads(get(server + "/api/pricing", cookie=ck)[2]) == {"contracts": []}
    code, _, body = get(server + "/api/run", cookie=ck, data=b'{"agent":"market_data"}')
    assert code == 200 and json.loads(body)["ok"]
    assert get(server + "/api/run", cookie=ck, data=b'{"agent":"rm -rf"}')[0] == 400
    with db.session() as con:
        assert db.kv_get(con, "orchestrator:requests") == ["market_data"]
