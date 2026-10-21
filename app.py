"""YTV server: yt-dlp ke upar chhota API. TV app sirf list leti hai aur link chalati hai.
Env: API_KEY (zaroor set karo), MAX_JOBS=2, MAX_HEIGHT=720, COOKIES_FILE ya COOKIES_B64 (optional), YT_PROXY (optional), DATABASE_URL (cookies ko restart ke baad bachane ke liye)
"""
import os, time, threading, base64
import requests
from flask import Flask, request, jsonify, Response
from yt_dlp import YoutubeDL

KEY = os.environ.get("API_KEY", "")
COOKIES = os.environ.get("COOKIES_FILE", "cookies.txt")
_b64 = os.environ.get("COOKIES_B64", "")  # cookies.txt ko base64 karke env mein (public image mein file mat daalo)
if _b64:
    COOKIES = os.path.join(os.environ.get("HOME", "."), "cookies.txt")
    with open(COOKIES, "wb") as _f:
        _f.write(base64.b64decode(_b64))
DB_URL = os.environ.get("DATABASE_URL", "")  # blitz.cloud PostgreSQL: restart/rebuild par bhi cookies bachengi
if DB_URL and not _b64:
    COOKIES = os.path.join(os.environ.get("HOME", "."), "cookies.txt")


def db_load():
    if not DB_URL:
        return None
    try:
        import psycopg
        with psycopg.connect(DB_URL, connect_timeout=10) as c:
            c.execute("create table if not exists kv(k text primary key, v text)")
            r = c.execute("select v from kv where k='cookies'").fetchone()
            return r[0] if r else None
    except Exception as e:
        print("db_load fail:", e, flush=True)


def db_save(txt):
    if not DB_URL:
        return False
    try:
        import psycopg
        with psycopg.connect(DB_URL, connect_timeout=10) as c:
            c.execute("create table if not exists kv(k text primary key, v text)")
            c.execute("insert into kv(k,v) values('cookies',%s) on conflict (k) do update set v=excluded.v", (txt,))
        return True
    except Exception as e:
        print("db_save fail:", e, flush=True)
        return False


_saved_mtime = 0.0


def sync_cookies():  # yt-dlp cookies badalta rehta hai; naya version DB mein bhi rakho (10 min mein ek baar)
    global _saved_mtime
    try:
        if DB_URL and os.path.isfile(COOKIES):
            m = os.path.getmtime(COOKIES)
            if m > _saved_mtime and time.time() - _saved_mtime > 600:
                with open(COOKIES, encoding="utf-8") as f:
                    if db_save(f.read()):
                        _saved_mtime = m
    except Exception:
        pass


_c = db_load()
if _c:
    with open(COOKIES, "w", encoding="utf-8") as _f:
        _f.write(_c)
    _saved_mtime = os.path.getmtime(COOKIES)

PROXY = os.environ.get("YT_PROXY", "")  # optional: http://user:pass@host:port ya socks5://host:port
CLIENTS = os.environ.get("YT_CLIENTS", "")  # jaise: tv,mweb  (probe se pata chalega kaun chalta hai)
MAXH = int(os.environ.get("MAX_HEIGHT", "720"))
PLAY_TTL, LIST_TTL = 5 * 3600, 600

app = Flask(__name__)
sem = threading.Semaphore(int(os.environ.get("MAX_JOBS", "2")))
_cache, _clock, _locks = {}, threading.Lock(), {}


def cget(k):
    with _clock:
        v = _cache.get(k)
        if v and v[0] > time.time():
            return v[1]
        _cache.pop(k, None)


def cset(k, val, ttl):
    with _clock:
        if len(_cache) > 600:
            now = time.time()
            for kk in [x for x, v in _cache.items() if v[0] < now]:
                _cache.pop(kk, None)
        _cache[k] = (time.time() + ttl, val)


def opts(**kw):
    o = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
         "socket_timeout": 15, "js_runtimes": {"node": {}}}
    if os.path.isfile(COOKIES):
        o["cookiefile"] = COOKIES
    if PROXY:
        o["proxy"] = PROXY
    if CLIENTS:
        o["extractor_args"] = {"youtube": {"player_client": [c.strip() for c in CLIENTS.split(",") if c.strip()]}}
    o.update(kw)
    return o


@app.before_request
def auth():
    if request.path == "/health" or not KEY:
        return
    if (request.headers.get("X-Key") or request.args.get("key")) != KEY:
        return jsonify(error="bad key"), 401


@app.errorhandler(Exception)
def err(e):
    m = str(e)
    low = m.lower()
    if "not a bot" in low or "sign in" in low or "confirm you" in low:
        return jsonify(error="bot", hint="YouTube ne bot check lagaya: /login se cookies daalo ya YT_PROXY lagao", detail=m[-200:]), 503
    return jsonify(error=m[-300:]), 500


def flat(url, n):
    k = f"l:{url}:{n}"
    hit = cget(k)
    if hit is not None:
        return hit
    with sem, YoutubeDL(opts(extract_flat=True, noplaylist=False, playlistend=n)) as y:
        info = y.extract_info(url, download=False)
    out = []
    for e in (info.get("entries") or []):
        vid = (e or {}).get("id") or ""
        if len(vid) != 11:
            continue
        out.append({"id": vid, "title": e.get("title") or "",
                    "channel": e.get("channel") or e.get("uploader") or "",
                    "duration": e.get("duration"), "views": e.get("view_count"),
                    "thumb": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"})
    cset(k, out, LIST_TTL)
    return out


def resolve(vid, h):
    k = f"p:{vid}:{h}"
    hit = cget(k)
    if hit:
        return hit
    with _clock:
        lk = _locks.setdefault(k, threading.Lock())
    with lk:  # prefetch aur play ek hi kaam do baar na karein
        hit = cget(k)
        if hit:
            return hit
        fmt = (f"bv*[vcodec^=avc1][height<={h}]+ba[ext=m4a]/"
               f"b[ext=mp4][height<={h}]/b[height<={h}]/b")
        t = time.time()
        with sem, YoutubeDL(opts(format=fmt)) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
        rf = info.get("requested_formats") or [info]
        v = next((f for f in rf if f.get("vcodec") not in (None, "none")), None)
        a = next((f for f in rf if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")), None)
        if not v:
            raise RuntimeError("koi video format nahi mila")
        res = {"id": vid, "title": info.get("title"), "height": v.get("height"),
               "video": v["url"], "audio": a["url"] if a else None,
               "ua": (v.get("http_headers") or {}).get("User-Agent", "Mozilla/5.0"),
               "took": round(time.time() - t, 1)}
        cset(k, res, PLAY_TTL)
        sync_cookies()
        return res


@app.get("/health")
def health():
    return jsonify(ok=True, cookies=os.path.isfile(COOKIES), db=bool(DB_URL))


@app.get("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(error="q chahiye"), 400
    n = min(int(request.args.get("n", 20)), 40)
    return jsonify(items=flat(f"ytsearch{n}:{q}", n))


@app.get("/channel")
def channel():
    cid = request.args.get("id", "")
    n = min(int(request.args.get("n", 30)), 50)
    url = f"https://www.youtube.com/channel/{cid}/videos" if cid.startswith("UC") else f"https://www.youtube.com/{cid}/videos"
    return jsonify(items=flat(url, n))


@app.get("/feed")
def feed():  # cookies chahiye. name=subs ya home (abhi test nahi hua)
    name = request.args.get("name", "subs")
    n = min(int(request.args.get("n", 30)), 50)
    return jsonify(items=flat({"subs": ":ytsubs", "home": ":ytrec"}.get(name, ":ytsubs"), n))


@app.get("/play")
def play():
    vid = request.args["id"]
    h = int(request.args.get("h", MAXH))
    r = dict(resolve(vid, h))
    if request.args.get("proxy", "1" if PROXY else "0") == "1":  # direct link 403 de to Blitz se hoke chalao
        base = request.host_url.rstrip("/")
        kq = f"&key={KEY}" if KEY else ""
        r["video"] = f"{base}/stream?id={vid}&k=v&h={h}{kq}"
        if r["audio"]:
            r["audio"] = f"{base}/stream?id={vid}&k=a&h={h}{kq}"
    return jsonify(r)


@app.get("/prefetch")
def prefetch():
    vid, h = request.args["id"], int(request.args.get("h", MAXH))
    threading.Thread(target=lambda: _safe(vid, h), daemon=True).start()
    return jsonify(ok=True)


def _safe(vid, h):
    try:
        resolve(vid, h)
    except Exception:
        pass


@app.get("/stream")
def stream():
    vid, h = request.args["id"], int(request.args.get("h", MAXH))
    r = resolve(vid, h)
    url = r["video"] if request.args.get("k", "v") == "v" else r["audio"]
    hd = {"User-Agent": r["ua"]}
    if request.headers.get("Range"):
        hd["Range"] = request.headers["Range"]
    px = {"http": PROXY, "https": PROXY} if PROXY else None
    up = requests.get(url, headers=hd, stream=True, timeout=20, proxies=px)
    keep = ("content-type", "content-length", "content-range", "accept-ranges")
    return Response(up.iter_content(64 * 1024), status=up.status_code,
                    headers={k: v for k, v in up.headers.items() if k.lower() in keep})


@app.get("/probe")
def probe():  # har YouTube client ko ek-ek karke try karta hai, batata hai kaun chala aur kaun bot check mein atka
    vid = request.args.get("id", "dQw4w9WgXcQ")
    out = []
    for c in ["default", "tv", "mweb", "web_safari", "ios", "android_vr", "web_embedded", "tv_simply"]:
        t = time.time()
        try:
            with sem, YoutubeDL(opts(format="b", extractor_args={"youtube": {"player_client": [c]}})) as y:
                info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            out.append({"client": c, "ok": True, "height": info.get("height"), "s": round(time.time() - t, 1)})
        except Exception as e:
            out.append({"client": c, "ok": False, "s": round(time.time() - t, 1), "err": str(e)[-150:]})
    return jsonify(cookies=os.path.isfile(COOKIES), proxy=bool(PROXY), results=out)


LOGIN_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<body style="font-family:sans-serif;max-width:640px;margin:20px auto;padding:0 12px">
<h3>YTV login (cookies)</h3><p>Status: <b>%s</b></p>
<p>Browser mein YouTube par login karke cookies.txt (Netscape format) export karo aur yahan paste karo.</p>
<form method=post><textarea name=c rows=12 style="width:100%%" placeholder="# Netscape HTTP Cookie File ..."></textarea>
<br><button style="padding:10px 18px;margin-top:8px">Save</button></form></body>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        txt = (request.form.get("c") or "").strip()
        if len(txt) > 300000 or ("youtube.com" not in txt and "google.com" not in txt):
            return Response("cookies.txt jaisa nahi lag raha", 400)
        with open(COOKIES, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        global _saved_mtime
        stored = db_save(txt + "\n")
        if stored:
            _saved_mtime = os.path.getmtime(COOKIES)
        with _clock:
            _cache.clear()
        note = "DB mein bhi save (restart par bachegi)." if stored else "DATABASE_URL nahi/fail: sirf is run tak bachegi."
        return Response("Saved. " + note + " Ab /feed?name=subs try karo.", 200)
    st = "cookies mili hui hain" if os.path.isfile(COOKIES) else "cookies nahi hain"
    return Response(LOGIN_HTML % st, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threaded=True)
