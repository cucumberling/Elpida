"""Dashboard 网站：系统总览、生猪行情、期权定价。

只监听 127.0.0.1（默认 8780 端口），外面经 Caddy 的 HTTPS 访问。网站本身只读数据库，不跑 agent；
点「立即运行」只是给总管留个请求。

登录：链接里带 ?key=钥匙，第一次打开后记在浏览器里 30 天。钥匙在 ~/elpida-data/web_token。
"""
import json
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from elpida import config, db, orchestrator, registry
from elpida.agents.pricing import KEY as PRICING_KEY
from elpida.skills import continuous, dce

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "elpida_key"
LWC_VER = "5.2.1"
LWC_URLS = [f"https://cdn.jsdelivr.net/npm/lightweight-charts@{LWC_VER}/dist/lightweight-charts.standalone.production.js",
            f"https://unpkg.com/lightweight-charts@{LWC_VER}/dist/lightweight-charts.standalone.production.js"]

_cache, _lock = {}, threading.Lock()


def token():
    try:
        return config.TOKEN_FILE.read_text().strip()
    except FileNotFoundError:
        return ""


def lwc():
    """图表库：第一次从 CDN 下载后存在数据目录，以后直接用。"""
    f = config.DATA / f"lightweight-charts-{LWC_VER}.js"
    if f.exists():
        return f.read_bytes()
    for u in LWC_URLS:
        try:
            r = requests.get(u, timeout=20)
            r.raise_for_status()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(r.content)
            return r.content
        except Exception:
            continue
    return b""


def _version(con, product):
    r = con.execute("SELECT COUNT(*) AS n, MAX(date) AS d FROM bars WHERE product=?", (product,)).fetchone()
    return (r["n"], r["d"])


def product_frames(con, product):
    """某品种所有合约的 DataFrame，数据没变就用缓存。"""
    ver = _version(con, product)
    with _lock:
        hit = _cache.get(product)
        if hit and hit[0] == ver:
            return hit[1]
    rows = con.execute("SELECT * FROM bars WHERE product=? ORDER BY date", (product,)).fetchall()
    frames = continuous.frames(rows)
    main = continuous.build_main(frames)
    with _lock:
        _cache[product] = (ver, (frames, main))
    return frames, main


def _day(ts):
    return ts.strftime("%Y-%m-%d")


def hog_payload(con):
    focus = config.FOCUS
    frames, main = product_frames(con, focus)
    if main.empty:
        return {"empty": True}
    bars = [{"time": _day(d), "open": round(r.open, 1), "high": round(r.high, 1), "low": round(r.low, 1),
             "close": round(r.close, 1), "volume": r.volume, "hold": r.hold, "contract": r.contract,
             "raw_close": r.raw_close} for d, r in main.iterrows()]
    act = continuous.active(frames)
    last_day = main.index[-1]
    curve_now = continuous.curve(frames, last_day)
    past = main.index[-21] if len(main) > 21 else main.index[0]
    curve_prev = continuous.curve(frames, past)
    for c in curve_now:
        c["expiry"] = dce.option_expiry(c["contract"]).isoformat()
        c["exit_by"] = dce.last_month_for_individuals(c["contract"]).isoformat()
    # 跨月价差：当前主力 减 下一张更远的活跃合约
    spread = None
    main_code = main["contract"].iloc[-1]
    mk = continuous.month_key(main_code)
    later = [c for c in act if continuous.month_key(c) > mk]
    earlier = [c for c in act if continuous.month_key(c) < mk]
    pair = (main_code, later[0]) if later else (earlier[-1], main_code) if earlier else None
    if pair:
        s = continuous.spread(frames, *pair)
        spread = {"near": pair[0], "far": pair[1],
                  "series": [{"time": _day(d), "value": round(v, 1)} for d, v in s.items()]}
    related = {}
    for p in config.PRODUCTS:
        if p == focus:
            continue
        _, m = product_frames(con, p)
        if not m.empty:
            related[p] = {"name": config.PRODUCTS[p][0],
                          "series": [{"time": _day(d), "value": round(v, 1)} for d, v in m["raw_close"].items()]}
    # 期货猪粮比：生猪主力价 ÷ 玉米主力价（同为 元/吨，用未复权的真实价格）
    ratio = []
    if "C" in related:
        corn = {x["time"]: x["value"] for x in related["C"]["series"]}
        ratio = [{"time": b["time"], "value": round(b["raw_close"] / corn[b["time"]], 2)}
                 for b in bars if corn.get(b["time"])]
    return {"product": focus, "name": config.PRODUCTS[focus][0], "bars": bars, "main": main_code,
            "last_day": _day(last_day), "curve": curve_now, "curve_prev": {"day": _day(past), "points": curve_prev},
            "spread": spread, "related": related, "ratio": ratio, "rolls": int((main["adj"].diff() != 0).sum() - 1)}


def status_payload(con):
    hb = db.kv_get(con, orchestrator.HEARTBEAT_KEY)
    data = []
    for p, (name, *_rest) in config.PRODUCTS.items():
        r = con.execute("SELECT COUNT(DISTINCT contract) AS n, MAX(date) AS d, COUNT(*) AS rows FROM bars WHERE product=?",
                        (p,)).fetchone()
        data.append({"product": p, "name": name, "contracts": r["n"], "last_day": r["d"], "rows": r["rows"]})
    recent = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 15")]
    return {"heartbeat": hb, "agents": orchestrator.status(con), "data": data, "recent_runs": recent,
            "skills": [{"name": n, "desc": d, "used_by": list(u)} for n, d, u in registry.SKILLS],
            "now": db.now()}


class Handler(BaseHTTPRequestHandler):
    server_version = "elpida"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def js(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False, default=str), "application/json; charset=utf-8")

    def authed(self, q):
        """(是否通过, 是否刚用网址里的钥匙登录)"""
        want = token()
        if not want:
            return False, False
        ck = SimpleCookie(self.headers.get("Cookie", ""))
        have = ck[COOKIE].value if COOKIE in ck else ""
        if have and secrets.compare_digest(have, want):
            return True, False
        url_key = (q.get("key") or [""])[0]
        if url_key and secrets.compare_digest(url_key, want):
            return True, True
        return False, False

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        ok, fresh = self.authed(q)
        if not ok:
            return self.send(401, (STATIC / "locked.html").read_bytes(), "text/html; charset=utf-8")
        if fresh:                       # 钥匙写进 cookie，再跳转到不带钥匙的地址（钥匙不留在地址栏和历史里）
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return self.send(302, b"", "text/plain", {
                "Set-Cookie": f"{COOKIE}={token()}; Max-Age=2592000; Path=/; HttpOnly; SameSite=Lax{secure}",
                "Location": u.path or "/"})
        if u.path == "/":
            return self.send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/static/lwc.js":
            return self.send(200, lwc(), "application/javascript")
        with db.session() as con:
            if u.path == "/api/status":
                return self.js(status_payload(con))
            if u.path == "/api/hog":
                return self.js(hog_payload(con))
            if u.path == "/api/pricing":
                return self.js(db.kv_get(con, PRICING_KEY.format(product=config.FOCUS)) or {"contracts": []})
        self.send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlparse(self.path)
        ok, _ = self.authed({})
        if not ok:
            return self.js({"error": "未登录"}, 401)
        if u.path == "/api/run":
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 10_000)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self.js({"error": "请求格式不对"}, 400)
            name = str(body.get("agent", ""))
            if name not in {a.name for a in registry.active_agents()}:
                return self.js({"error": "没有这个 agent"}, 400)
            with db.session() as con:
                orchestrator.request_run(con, name)
            return self.js({"ok": True, "msg": "已通知总管，半分钟内开始运行"})
        self.js({"error": "not found"}, 404)


def serve(port=None):
    port = port or config.PORT
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Elpida 网站：http://127.0.0.1:{port}")
    httpd.serve_forever()
