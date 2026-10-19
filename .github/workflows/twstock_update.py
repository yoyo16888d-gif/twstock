#!/usr/bin/env python3
"""
台股族群看盤 — 每日資料更新程式

用途：從證交所(TWSE)與櫃買中心(TPEx)的公開資料，下載全部上市櫃股票的日K，
存成 data.js，讓同資料夾的「台股族群看盤.html」直接讀取。

第一次執行：自動回補最近約 6 個月（約 130 個交易日），大約需要 15~25 分鐘
          （證交所限制連線頻率，程式每次請求會自動等幾秒，請耐心等）。
之後每天執行：只補上缺少的交易日，幾十秒就完成。建議每天 14:30 以後執行。

只用 Python 內建模組，不需要安裝任何套件。
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.parse, ssl

HERE = os.path.dirname(os.path.abspath(__file__))
HIST_FILE = os.path.join(HERE, "history.json")
OUT_FILE = os.path.join(HERE, "data.js")
DAYS_WANTED = int(os.environ.get("TW_DAYS", "130"))   # 要保留幾個交易日
SLEEP = 3.5                                             # 每次請求間隔秒數（太快會被證交所暫時封鎖）
UA = {"User-Agent": "Mozilla/5.0 (twstock-board personal use)"}

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def http(url, data=None):
    req = urllib.request.Request(url, data=data, headers=UA)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            log(f"  連線失敗（第 {attempt+1} 次）：{e}")
            time.sleep(10 * (attempt + 1))
    return None

def num(s):
    if s is None: return None
    s = re.sub(r"<[^>]+>", "", str(s)).replace(",", "").strip()
    if s in ("", "--", "---", "----", "除權息", "除息", "除權"): return None
    try: return float(s)
    except ValueError:
        m = re.search(r"[-+]?\d+(\.\d+)?", s)
        return float(m.group()) if m else None

def find_idx(fields, *keys):
    f = [re.sub(r"\s+", "", x) for x in fields]
    for k in keys:
        for i, x in enumerate(f):
            if x == k: return i
    for k in keys:
        for i, x in enumerate(f):
            if k in x: return i
    return None

def rows_to_quotes(fields, data, sign_col=False):
    """Return {code: [open, high, low, close, volume_shares, value]}"""
    iC = find_idx(fields, "證券代號", "代號")
    iO = find_idx(fields, "開盤價", "開盤")
    iH = find_idx(fields, "最高價", "最高")
    iL = find_idx(fields, "最低價", "最低")
    iZ = find_idx(fields, "收盤價", "收盤")
    iV = find_idx(fields, "成交股數")
    iA = find_idx(fields, "成交金額", "成交金額(元)")
    if None in (iC, iO, iH, iL, iZ): return {}
    out = {}
    for r in data:
        code = re.sub(r"<[^>]+>", "", str(r[iC])).strip()
        if not re.fullmatch(r"\d{4}[A-Z]?", code): continue   # 只要一般股票代號（排除權證等）
        o, h, l, c = num(r[iO]), num(r[iH]), num(r[iL]), num(r[iZ])
        if not c: continue
        v = num(r[iV]) if iV is not None else None
        a = num(r[iA]) if iA is not None else None
        out[code] = [o or c, h or c, l or c, c, v or 0, a or 0]
    return out

# ---------- TWSE ----------
def twse_day(d):
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json"
    txt = http(url)
    if not txt: return None
    try: j = json.loads(txt)
    except Exception: return None
    for t in j.get("tables", []) or []:
        f = t.get("fields") or []
        if find_idx(f, "證券代號") is not None and find_idx(f, "收盤價") is not None:
            return rows_to_quotes(f, t.get("data") or [])
    # 舊版格式
    for k, v in j.items():
        if k.startswith("fields") and isinstance(v, list) and find_idx(v, "證券代號") is not None:
            return rows_to_quotes(v, j.get(k.replace("fields", "data")) or [])
    return {}   # 有回應但沒資料 = 休市日

# ---------- TPEx ----------
def tpex_parse(txt):
    try: j = json.loads(txt)
    except Exception: return None
    tables = j.get("tables") or []
    for t in tables:
        f = t.get("fields") or []
        if find_idx(f, "代號") is not None and find_idx(f, "收盤") is not None:
            return rows_to_quotes(f, t.get("data") or [])
    if "aaData" in j:  # 舊版：代號,名稱,收盤,漲跌,開盤,最高,最低,均價,成交股數,成交金額
        f = ["代號","名稱","收盤","漲跌","開盤","最高","最低","均價","成交股數","成交金額"]
        return rows_to_quotes(f, j["aaData"])
    return {}

def tpex_day(d):
    ds = f"{d:%Y/%m/%d}"
    url = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
    q = urllib.parse.urlencode({"date": ds, "id": "", "response": "json"})
    for txt in (http(url, q.encode()), http(url + "?" + q)):
        if txt:
            r = tpex_parse(txt)
            if r: return r
    # 舊網址
    roc = f"{d.year-1911}/{d:%m/%d}"
    txt = http(f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d={roc}&o=json")
    if txt:
        r = tpex_parse(txt)
        if r: return r
    return {}

def tpex_latest_openapi():
    txt = http("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes")
    if not txt: return None, {}
    try: arr = json.loads(txt)
    except Exception: return None, {}
    out, date = {}, None
    for o in arr:
        code = str(o.get("SecuritiesCompanyCode", "")).strip()
        if not re.fullmatch(r"\d{4}[A-Z]?", code): continue
        c = num(o.get("Close"))
        if not c: continue
        out[code] = [num(o.get("Open")) or c, num(o.get("High")) or c, num(o.get("Low")) or c, c,
                     num(o.get("TradingShares")) or 0, num(o.get("TransactionAmount")) or 0]
        date = date or str(o.get("Date", ""))
    m = re.match(r"(\d{3})(\d{2})(\d{2})", date or "")
    iso = f"{int(m.group(1))+1911}-{m.group(2)}-{m.group(3)}" if m else None
    return iso, out

# ---------- main ----------
def load_hist():
    try:
        with open(HIST_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_hist(h):
    tmp = HIST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(h, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, HIST_FILE)

def write_js(h):
    dates = sorted(h)[-DAYS_WANTED:]
    series = {}
    for di, d in enumerate(dates):
        for code, q in h[d].items():
            series.setdefault(code, []).append([di] + [round(x, 2) if isinstance(x, float) and x < 1e7 else int(x or 0) for x in q[:4]] + [int(round((q[4] or 0) / 1000)), int(round((q[5] or 0) / 1e5))])
    payload = {"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "dates": dates, "s": series}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("window.TW_DATA=")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    log(f"已寫出 data.js：{len(dates)} 個交易日、{len(series)} 檔股票（{dates[0]} ~ {dates[-1]}）")

def main():
    h = load_hist()
    today = datetime.date.today()
    now = datetime.datetime.now()
    # 收盤資料約 14:00 後公布；太早執行就從昨天開始
    d = today if now.hour >= 14 else today - datetime.timedelta(days=1)
    have = set(h)
    need_total = DAYS_WANTED
    checked = 0
    first_run = len(h) == 0
    if first_run:
        log(f"第一次執行，開始回補最近 {DAYS_WANTED} 個交易日（每個交易日約 10 秒，請耐心等候）…")
    trading_seen = 0
    while trading_seen < need_total and checked < DAYS_WANTED * 2 + 30:
        checked += 1
        if d.weekday() >= 5:            # 週末
            d -= datetime.timedelta(days=1); continue
        key = d.isoformat()
        if key in have:
            trading_seen += 1
            if not first_run and trading_seen > 5: break   # 已經接上舊資料，停止往回找
            d -= datetime.timedelta(days=1); continue
        log(f"下載 {key} …")
        tw = twse_day(d); time.sleep(SLEEP)
        if tw is None:
            log("  證交所沒有回應，稍後重新執行即可續傳。"); break
        if not tw:
            log("  休市，跳過"); d -= datetime.timedelta(days=1); continue
        tp = tpex_day(d); time.sleep(SLEEP)
        if not tp: log("  ⚠ 上櫃資料取得失敗，這天只有上市資料")
        h[key] = {**tw, **(tp or {})}
        trading_seen += 1
        log(f"  上市 {len(tw)} 檔、上櫃 {len(tp or {})} 檔")
        save_hist(h)                     # 每天存一次，中斷也能續傳
        d -= datetime.timedelta(days=1)
    # 用 OpenAPI 補最新一天的上櫃（新網址失效時的備援）
    latest = max(h) if h else None
    if latest and len(h[latest]) < 1500:
        iso, q = tpex_latest_openapi()
        if iso and iso in h and q:
            h[iso].update(q); save_hist(h); log(f"已用 OpenAPI 補上 {iso} 上櫃 {len(q)} 檔")
    # 只保留需要的天數
    for k in sorted(h)[:-DAYS_WANTED]: h.pop(k)
    save_hist(h)
    if h: write_js(h)
    else: log("沒有取得任何資料，請確認網路連線後再試。")

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: log("已中斷，下次執行會從中斷處繼續。")
    if os.name == "nt" and sys.stdin and sys.stdin.isatty() and "--nopause" not in sys.argv:
        input("\n按 Enter 關閉視窗…")
