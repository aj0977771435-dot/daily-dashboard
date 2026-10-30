"""每日看板後端：定時抓資料、存快取，並提供網頁與 /api/data 給前端。"""

import json
import os
import re
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from html import unescape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
CACHE_PATH = BASE / "cache.json"
STATIC_DIR = BASE / "static"
PORT = int(os.getenv("DASHBOARD_PORT", "8000"))

TPE = ZoneInfo("Asia/Taipei")
NY = ZoneInfo("America/New_York")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

load_dotenv(BASE / ".env")
ALPHA_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
ALPHA_DAILY_LIMIT = 24          # 免費版一天 25 次，留 1 次餘裕
ALPHA_MIN_GAP = 30 * 60         # 同一檔美股最快 30 分鐘才再用一次 Alpha Vantage

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})

lock = threading.Lock()
wake = threading.Event()


def now_tpe():
    return datetime.now(TPE)


def iso(dt):
    return dt.astimezone(TPE).isoformat(timespec="seconds")


def log(msg):
    print(f"[{now_tpe():%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- 設定與快取

config = {}
config_mtime = 0.0


def load_config():
    """config.json 有變動就重新讀取；格式錯誤時沿用舊設定。"""
    global config, config_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
        if mtime == config_mtime:
            return False
        with open(CONFIG_PATH, encoding="utf-8") as f:
            new = json.load(f)
        config, config_mtime = new, mtime
        log("已載入 config.json")
        return True
    except Exception as e:
        log(f"config.json 讀取失敗，沿用舊設定：{e}")
        config_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
        return False


def refresh(key, default):
    return config.get("refresh", {}).get(key, default)


cache = {"stocks": {}, "nba": {}, "products": {}, "alpha": {}, "next": {}}
if CACHE_PATH.exists():
    try:
        cache.update(json.loads(CACHE_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass


def save_cache():
    tmp = CACHE_PATH.with_suffix(".tmp")
    with lock:
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def store(section, key, data):
    """成功就覆蓋；失敗就保留上一次的數字，只標記錯誤。"""
    with lock:
        old = cache[section].get(key, {})
        if data.get("ok"):
            data["fetched"] = iso(now_tpe())
            cache[section][key] = data
        else:
            old = dict(old)
            old.update({"ok": False, "error": data.get("error", "暫時抓不到"),
                        "failed_at": iso(now_tpe())})
            for k in ("symbol", "name", "market"):
                if k in data:
                    old.setdefault(k, data[k])
            cache[section][key] = old


def get_json(url, **kw):
    r = session.get(url, timeout=15, **kw)
    r.raise_for_status()
    return r.json()


def num(s):
    try:
        v = float(str(s).replace(",", ""))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 市場時間

def tw_market_open(t=None):
    t = t or now_tpe()
    return t.weekday() < 5 and (9, 0) <= (t.hour, t.minute) <= (13, 35)


def us_market_open(t=None):
    t = (t or now_tpe()).astimezone(NY)   # 用紐約時間判斷，自動處理夏令／冬令
    return t.weekday() < 5 and (9, 30) <= (t.hour, t.minute) <= (16, 5)


# ---------------------------------------------------------------- 台股

def fetch_tw(stocks):
    if not stocks:
        return
    ex = "|".join(f"{m}_{s['symbol']}.tw" for s in stocks for m in ("tse", "otc"))
    got = set()
    try:
        data = get_json(f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex}&_={int(time.time()*1000)}")
        for row in data.get("msgArray", []):
            sym = row.get("c")
            prev = num(row.get("y"))
            # z 是這一筆快照的成交價；剛好這幾秒沒成交時是 "-"，改用 trade.z（最近一筆成交）
            price, t = num(row.get("z")), row.get("t", "")
            last = row.get("trade") or {}
            if price is None and num(last.get("z")):
                price, t = num(last.get("z")), last.get("t") or t
            bid = num((row.get("b") or "").split("_")[0])
            ask = num((row.get("a") or "").split("_")[0])
            d = row.get("d", "")
            item = {"ok": True, "symbol": sym, "name": row.get("n"), "market": "TW", "ex": row.get("ex"),
                    "prev_close": prev, "price": price, "bid": bid, "ask": ask,
                    "currency": "TWD", "source": "證交所即時",
                    "data_time": f"{d[:4]}-{d[4:6]}-{d[6:]} {t}"}
            if price is None and not tw_market_open():
                continue        # 非盤中又沒有成交價，交給收盤資料
            if price is None:
                item["note"] = "尚無成交"
            store("stocks", sym, item)
            got.add(sym)
    except Exception as e:
        log(f"台股即時失敗：{e}")

    missing = [s for s in stocks if s["symbol"] not in got]
    if not missing:
        return
    try:
        rows = {r["Code"]: r for r in get_json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")}
        for s in missing:
            r = rows.get(s["symbol"])
            if not r:
                store("stocks", s["symbol"], {"ok": False, "symbol": s["symbol"], "name": s.get("name"), "market": "TW"})
                continue
            close = num(r.get("ClosingPrice"))
            change = float(r.get("Change") or 0)
            roc = r.get("Date", "")   # 民國年，例如 1151029
            date = f"{int(roc[:-4]) + 1911}-{roc[-4:-2]}-{roc[-2:]}" if len(roc) >= 7 else ""
            store("stocks", s["symbol"], {
                "ok": True, "symbol": s["symbol"], "name": r.get("Name") or s.get("name"), "market": "TW",
                "price": close, "prev_close": round(close - change, 4) if close else None,
                "currency": "TWD", "source": "證交所收盤", "data_time": f"{date} 收盤"})
    except Exception as e:
        log(f"台股收盤資料失敗：{e}")
        for s in missing:
            store("stocks", s["symbol"], {"ok": False, "symbol": s["symbol"], "name": s.get("name"), "market": "TW"})


# ---------------------------------------------------------------- 美股

def fetch_us_yahoo(sym):
    data = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d")
    meta = data["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("previousClose") or meta.get("chartPreviousClose")
    if price is None or prev is None:
        raise ValueError("缺少價格欄位")
    ts = meta.get("regularMarketTime")
    return {"price": price, "prev_close": prev, "name": meta.get("shortName") or meta.get("longName"),
            "data_time": iso(datetime.fromtimestamp(ts, timezone.utc))[:16].replace("T", " ") if ts else "",
            "source": "Yahoo Finance"}


def fetch_us_alpha(sym):
    if not ALPHA_KEY:
        raise ValueError("沒有設定 Alpha Vantage 金鑰")
    today = now_tpe().strftime("%Y-%m-%d")
    a = cache["alpha"]
    if a.get("date") != today:
        a.clear()
        a.update({"date": today, "count": 0, "last": {}})
    if a["count"] >= ALPHA_DAILY_LIMIT:
        raise ValueError("Alpha Vantage 今日次數已用完")
    if time.time() - a["last"].get(sym, 0) < ALPHA_MIN_GAP:
        raise ValueError("Alpha Vantage 冷卻中")
    a["count"] += 1
    a["last"][sym] = time.time()
    data = get_json("https://www.alphavantage.co/query",
                    params={"function": "GLOBAL_QUOTE", "symbol": sym, "apikey": ALPHA_KEY})
    q = data.get("Global Quote") or {}
    price, prev = num(q.get("05. price")), num(q.get("08. previous close"))
    if price is None or prev is None:
        raise ValueError(data.get("Note") or data.get("Information") or "Alpha Vantage 沒有回傳資料")
    return {"price": price, "prev_close": prev, "data_time": f"{q.get('07. latest trading day', '')} 收盤/最新",
            "source": "Alpha Vantage"}


def fetch_us(stocks):
    for s in stocks:
        sym = s["symbol"]
        base = {"symbol": sym, "name": s.get("name"), "market": "US", "currency": "USD"}
        for fn in (fetch_us_yahoo, fetch_us_alpha):
            try:
                d = fn(sym)
                d["name"] = s.get("name") or d.get("name")
                store("stocks", sym, {**base, **d, "ok": True})
                break
            except Exception as e:
                log(f"美股 {sym} {fn.__name__} 失敗：{e}")
        else:
            store("stocks", sym, {**base, "ok": False})


# ---------------------------------------------------------------- NBA（資料來源：ESPN 公開比分）

TEAM_ZH = {
    "ATL": "老鷹", "BOS": "塞爾提克", "BKN": "籃網", "CHA": "黃蜂", "CHI": "公牛",
    "CLE": "騎士", "DAL": "獨行俠", "DEN": "金塊", "DET": "活塞", "GSW": "勇士",
    "HOU": "火箭", "IND": "溜馬", "LAC": "快艇", "LAL": "湖人", "MEM": "灰熊",
    "MIA": "熱火", "MIL": "公鹿", "MIN": "灰狼", "NOP": "鵜鶘", "NYK": "尼克",
    "OKC": "雷霆", "ORL": "魔術", "PHI": "76人", "PHX": "太陽", "POR": "拓荒者",
    "SAC": "國王", "SAS": "馬刺", "TOR": "暴龍", "UTA": "爵士", "WAS": "巫師",
}
# ESPN 有幾隊的縮寫跟 NBA 官方不同
ESPN_CODE = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}


def team(c):
    t = c.get("team", {})
    code = ESPN_CODE.get(t.get("abbreviation", ""), t.get("abbreviation", ""))
    score = c.get("score")
    return {"code": code, "name": TEAM_ZH.get(code, t.get("shortDisplayName") or code or "待定"),
            "score": int(score) if str(score or "").isdigit() else None}


def norm_game(e):
    comp = e["competitions"][0]
    side = {c.get("homeAway"): team(c) for c in comp.get("competitors", [])}
    st = e.get("status") or comp.get("status") or {}
    state = (st.get("type") or {}).get("state")
    start = datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(TPE)
    return {
        "id": e["id"], "status": {"pre": "upcoming", "in": "live", "post": "final"}.get(state, "upcoming"),
        "status_text": (st.get("type") or {}).get("shortDetail", ""), "period": st.get("period") or 0,
        "clock": st.get("displayClock", ""), "start": iso(start), "date": start.strftime("%Y-%m-%d"),
        "away": side.get("away", {}), "home": side.get("home", {}),
        "url": f"https://www.espn.com/nba/game/_/gameId/{e['id']}",
    }


def fetch_nba():
    # 台灣的昨天～明天，大約對應美國日期的前天～今天；多抓一天比較保險，再依台灣日期分組
    today = now_tpe().date()
    wanted = {(today + timedelta(days=d)).isoformat() for d in (-1, 0, 1)}
    try:
        games = {}
        for d in range(-2, 2):
            data = get_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                            params={"dates": f"{today + timedelta(days=d):%Y%m%d}"})
            for e in data.get("events", []):
                games[e["id"]] = norm_game(e)
        games = games.values()
        games = sorted((g for g in games if g["date"] in wanted), key=lambda g: g["start"])
        with lock:
            cache["nba"] = {"ok": True, "fetched": iso(now_tpe()), "games": games, "source": "ESPN"}
    except Exception as e:
        log(f"NBA 失敗：{e}")
        with lock:
            cache["nba"].update({"ok": False, "error": str(e)})
    return any(g["status"] == "live" for g in cache["nba"].get("games", []))


# ---------------------------------------------------------------- 3C 價格

def find_prices(obj):
    """從 JSON-LD 裡遞迴找 price / lowPrice。"""
    out = []
    if isinstance(obj, dict):
        for k in ("price", "lowPrice"):
            if k in obj and num(obj[k]):
                out.append(num(obj[k]))
        for v in obj.values():
            out += find_prices(v)
    elif isinstance(obj, list):
        for v in obj:
            out += find_prices(v)
    return out


def extract_price(html):
    prices = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            prices += find_prices(json.loads(unescape(m.group(1).strip())))
        except Exception:
            pass
    if not prices:
        for pat in (r'<meta[^>]+(?:product:price:amount|og:price:amount)[^>]+content="([\d,.]+)"',
                    r'<meta[^>]+content="([\d,.]+)"[^>]+(?:product:price:amount|og:price:amount)',
                    r'itemprop="price"[^>]+content="([\d,.]+)"'):
            prices += [num(x) for x in re.findall(pat, html, re.I) if num(x)]
    prices = [p for p in prices if p >= 100]     # 濾掉運費、點數之類的小數字
    return min(prices) if prices else None


def ld_products(html):
    """從搜尋結果頁的 JSON-LD 取出每個商品的名稱、價格、網址（momo、Yahoo 購物中心都有）。"""
    out = []

    def walk(o):
        if isinstance(o, dict):
            offer = o.get("offers")
            if o.get("@type") == "Product" and isinstance(offer, dict):
                out.append({"title": o.get("name", ""), "price": num(offer.get("price") or offer.get("lowPrice")),
                            "url": offer.get("url") or o.get("url")})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            walk(json.loads(unescape(m.group(1).strip())))
        except Exception:
            pass
    return out


def search_pchome(q):
    data = get_json("https://ecshweb.pchome.com.tw/search/v4.3/all/results",
                    params={"q": q, "page": 1, "pageCount": 40})
    return [{"title": p.get("Name", ""), "price": num(p.get("Price")),
             "url": f"https://24h.pchome.com.tw/prod/{p['Id']}"} for p in data.get("Prods", [])]


def search_momo(q):
    r = session.get(f"https://www.momoshop.com.tw/search/{quote(q)}", timeout=20)
    r.raise_for_status()
    return ld_products(r.text)


def search_yahoo(q):
    r = session.get("https://tw.buy.yahoo.com/search/product", params={"p": q}, timeout=20)
    r.raise_for_status()
    return ld_products(r.text)


SHOPS = {
    "pchome": ("PChome 24h", search_pchome),
    "momo": ("momo購物網", search_momo),
    "yahoo": ("Yahoo購物中心", search_yahoo),
}
# 這些字出現在標題裡就不算（不是全新正品）
ALWAYS_EXCLUDE = ["福利", "整新", "二手", "中古", "展示", "拆封", "九成新", "租賃", "出租"]


def squash(s):
    """比對用：轉小寫、去掉空白和符號、256GB 當成 256G。"""
    s = re.sub(r"[^0-9a-z一-鿿]", "", str(s).lower())
    return s.replace("gb", "g")


def keyword_tokens(keyword):
    """'iPhone 17 Pro 256GB' → ['iphone17', 'pro', '256g']；純數字跟前一個字黏在一起（Switch 2 → switch2）。"""
    tokens = []
    for w in str(keyword).split():
        w = squash(w)
        if not w:
            continue
        if w.isdigit() and tokens:
            tokens[-1] += w
        else:
            tokens.append(w)
    return tokens


def is_match(title, tokens, exclude):
    """關鍵字要依序出現在標題裡（避免「990 PRO SSD 適用 PS5」被當成 PS5 Pro），而且不含排除字。"""
    t = squash(title)
    if any(squash(x) and squash(x) in t for x in exclude):
        return False
    pos = 0
    for tok in tokens:
        i = t.find(tok, pos)
        if i < 0:
            return False
        pos = i + len(tok)
    return True


def fetch_products(products):
    low, high = config.get("price_match_range", [0.7, 1.3])
    shops = [s for s in config.get("shops", list(SHOPS)) if s in SHOPS]
    for p in products:
        name = p["name"]
        official = num(p.get("official_price"))
        keyword = (p.get("search") or name).strip()
        tokens = keyword_tokens(keyword)
        exclude = ALWAYS_EXCLUDE + [x for x in p.get("exclude", []) if str(x).strip()]
        offers, errors, searched = [], [], []

        # 1) 自動到各通路搜尋，挑出真的是這個商品、價格合理的
        for key in shops:
            label, fn = SHOPS[key]
            try:
                found = 0
                for r in fn(keyword):
                    price = r.get("price")
                    if not price or not r.get("url") or not is_match(r["title"], tokens, exclude):
                        continue
                    if official and not (official * low <= price <= official * high):
                        continue          # 價格差太多，多半是配件或組合包
                    offers.append({"store": label, "price": price, "url": r["url"], "title": r["title"]})
                    found += 1
                searched.append({"store": label, "found": found})
            except Exception as e:
                errors.append(f"{label}：{e}")
            time.sleep(2)

        # 沒填官方價：改用各家價格的中位數當基準，濾掉防滑貼這類便宜配件
        if not official and offers:
            prices = sorted(o["price"] for o in offers)
            mid = prices[len(prices) // 2]
            offers = [o for o in offers if 0.6 * mid <= o["price"] <= 1.6 * mid]

        # 2) 另外指定的商品頁
        for entry in p.get("price_urls", []):
            url = entry["url"] if isinstance(entry, dict) else entry
            shop = entry.get("store") if isinstance(entry, dict) else None
            shop = shop or urlparse(url).netloc.replace("www.", "")
            try:
                r = session.get(url, timeout=20)
                r.raise_for_status()
                price = extract_price(r.text)
                if price:
                    offers.append({"store": shop, "price": price, "url": url, "title": "指定的商品頁"})
                else:
                    errors.append(f"{shop}：頁面上找不到價格")
            except Exception as e:
                errors.append(f"{shop}：{e}")
            time.sleep(2)

        offers.sort(key=lambda o: o["price"])
        # 每家店只留最便宜的一筆，方便比較
        per_store = []
        for o in offers:
            if all(x["store"] != o["store"] for x in per_store):
                per_store.append(o)
        if offers or not errors:
            store("products", name, {"ok": True, "name": name, "keyword": keyword, "best": offers[0] if offers else None,
                                     "offers": per_store, "matched": len(offers), "searched": searched,
                                     "errors": errors})
        else:
            store("products", name, {"ok": False, "name": name, "error": "；".join(errors)})
        for e in errors:
            log(f"3C {name} {e}")
        log(f"3C {name}：比對到 {len(offers)} 筆" + (f"，最低 {offers[0]['store']} {offers[0]['price']:.0f}" if offers else ""))


# ---------------------------------------------------------------- 排程

def due(key):
    return time.time() >= cache["next"].get(key, 0)


def schedule(key, seconds):
    cache["next"][key] = time.time() + seconds


def worker():
    nba_live = False
    while True:
        changed = load_config()
        if changed:
            # 股票、NBA 立刻依新設定重抓；3C 只有商品清單變了才重抓（下面比對 products_sig）
            cache["next"] = {k: v for k, v in cache["next"].items() if k.startswith("products")}
        stocks = config.get("stocks", [])
        tw = [s for s in stocks if s.get("market", "").upper() == "TW"]
        us = [s for s in stocks if s.get("market", "").upper() == "US"]
        try:
            if due("tw"):
                fetch_tw(tw)
                schedule("tw", refresh("tw_market_open_seconds", 60) if tw_market_open()
                         else refresh("tw_market_closed_seconds", 1800))
            if due("us"):
                fetch_us(us)
                schedule("us", refresh("us_market_open_seconds", 60) if us_market_open()
                         else refresh("us_market_closed_seconds", 1800))
            if due("nba"):
                nba_live = fetch_nba()
                schedule("nba", refresh("nba_live_seconds", 30) if nba_live
                         else refresh("nba_idle_seconds", 600))
            # 商品清單、比價通路或程式版本有改，也立刻重抓一次
            sig = "v2:" + json.dumps([config.get("products", []), config.get("shops"),
                                      config.get("price_match_range")], sort_keys=True, ensure_ascii=False)
            if due("products") or cache["next"].get("products_sig") != sig:
                fetch_products(config.get("products", []))
                schedule("products", refresh("products_hours", 24) * 3600)
                cache["next"]["products_sig"] = sig
        except Exception as e:
            log(f"排程錯誤：{e}")
        save_cache()
        wake.wait(5)
        wake.clear()


# ---------------------------------------------------------------- 網頁伺服器

def api_payload():
    with lock:
        stocks = []
        for s in config.get("stocks", []):
            d = dict(cache["stocks"].get(s["symbol"], {"ok": False, "error": "載入中…"}))
            d.update({"symbol": s["symbol"], "market": s.get("market", "").upper()})
            d["name"] = s.get("name") or d.get("name") or s["symbol"]
            stocks.append(d)
        products = []
        for p in config.get("products", []):
            d = dict(cache["products"].get(p["name"], {"ok": False, "error": "載入中…"}))
            d.update({"name": p["name"], "official_price": p.get("official_price"),
                      "official_url": p.get("official_url", "")})
            products.append(d)
        nba = {k: v for k, v in cache["nba"].items()}
    return {"now": iso(now_tpe()), "today": now_tpe().strftime("%Y-%m-%d"),
            "stocks": stocks, "products": products, "nba": nba,
            "hupu_url_template": config.get("hupu_url_template", "")}


SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-^]{1,12}$")


def clean_settings(body):
    """檢查網頁送來的自選股與商品清單；有問題就丟出中文錯誤訊息。"""
    stocks, products = [], []
    for s in body.get("stocks", []):
        sym = str(s.get("symbol", "")).strip().upper()
        market = str(s.get("market", "")).upper()
        if not sym:
            continue
        if not SYMBOL_RE.match(sym):
            raise ValueError(f"股票代號「{sym}」格式不對")
        if market not in ("TW", "US"):
            raise ValueError(f"{sym} 要選台股或美股")
        if any(x["symbol"] == sym and x["market"] == market for x in stocks):
            raise ValueError(f"{sym} 重複了")
        stocks.append({"symbol": sym, "name": str(s.get("name", "")).strip(), "market": market})
    for p in body.get("products", []):
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        if any(x["name"] == name for x in products):
            raise ValueError(f"商品「{name}」重複了")
        raw_price = str(p.get("official_price") or "").strip()
        price = num(raw_price)
        if raw_price and price is None:
            raise ValueError(f"「{name}」的官方價要填數字（不知道可以留空）")
        official = str(p.get("official_url", "")).strip()
        urls = []
        for u in p.get("price_urls", []):
            url = str(u.get("url", "")).strip()
            if not url:
                continue
            if not re.match(r"^https?://", url):
                raise ValueError(f"「{name}」的查價網址要以 https:// 開頭")
            urls.append({"store": str(u.get("store", "")).strip(), "url": url})
        if official and not re.match(r"^https?://", official):
            raise ValueError(f"「{name}」的官網網址要以 https:// 開頭")
        exclude = p.get("exclude", [])
        if isinstance(exclude, str):
            exclude = re.split(r"[,，、\s]+", exclude)
        if price is not None and price == int(price):
            price = int(price)
        products.append({"name": name, "official_price": price,
                         "official_url": official, "search": str(p.get("search", "")).strip(),
                         "exclude": [str(x).strip() for x in exclude if str(x).strip()],
                         "price_urls": urls})
    return stocks, products


def save_settings(body):
    global config, config_mtime
    stocks, products = clean_settings(body)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        full = json.load(f)              # 保留更新頻率等其他設定
    full["stocks"], full["products"] = stocks, products
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(full, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    wake.set()                           # 讓背景程式馬上讀新設定、重抓資料
    log("網頁上儲存了新的自選股／商品設定")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC_DIR), **kw)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/data"):
            self.send_json(api_payload())
        elif self.path.startswith("/api/settings"):
            with lock:
                self.send_json({"stocks": config.get("stocks", []), "products": config.get("products", [])})
        else:
            super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/settings"):
            return self.send_json({"error": "找不到"}, 404)
        # 只接受看板自己的網頁送來的修改，避免其他網站偷改設定
        origin = self.headers.get("Origin", "")
        if origin not in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}") \
                or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.send_json({"error": "不允許"}, 403)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 200_000:
                raise ValueError("資料太大")
            save_settings(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_json({"ok": True})
        except Exception as e:
            self.send_json({"error": str(e)}, 400)

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *a):
        pass


class Server(ThreadingHTTPServer):
    # Windows 預設允許兩個程式搶同一個埠；關掉它，才能偵測「看板已經開著」
    allow_reuse_address = False


def main():
    load_config()
    try:
        httpd = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        print("看板好像已經在執行中，直接幫你打開瀏覽器。")
        if os.getenv("NO_BROWSER") != "1":
            webbrowser.open(f"http://localhost:{PORT}")
        return
    threading.Thread(target=worker, daemon=True).start()
    print("=" * 50)
    print(f"  每日看板已啟動：http://localhost:{PORT}")
    print("  要關閉看板，直接關掉這個黑色視窗即可。")
    print("=" * 50, flush=True)
    if os.getenv("NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
