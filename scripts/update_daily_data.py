"""Daily ALL-NSE + ALL-BSE (~5,000+ Listed Shares) & India Policy News Auto-Updater.

Runs automatically via GitHub Actions every weekday at 4:00 PM IST:
1. Fetches official NSE Equity Master (EQUITY_L.csv) -> all ~2,200+ NSE listed equities.
2. Fetches official NSE Daily Full Bhavcopy (sec_bhavdata_full_DDMMYYYY.csv) -> daily NSE Close,
   Prev Close, 1D %, High, Low, and Delivery %.
3. Fetches official BSE Daily UDiFF Equity Bhavcopy (BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV) ->
   all ~4,000+ active BSE listed equities with 6-digit BSE Scrip Codes, ISIN, and daily prices.
4. Merges NSE + BSE by ISIN into market_all_stocks.json (tagged NSE+BSE, NSE, or BSE).
5. Enriches featured leaders + my_holdings.json with 52W High/Low via yfinance into daily_data.json.
"""

import csv
from datetime import datetime, timezone, timedelta
import io
import json
import urllib.request
import xml.etree.ElementTree as ET
import yfinance as yf

NSE_EQUITY_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_BHAVCOPY_URL_FMT = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
BSE_UDIFF_BHAVCOPY_URL_FMT = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/csv,text/plain,*/*",
}

DEFAULT_NSE_SYMBOLS = [
    "HDFCBANK", "ICICIBANK", "SBIN", "BAJFINANCE", "RELIANCE",
    "TCS", "INFY", "HCLTECH", "BHARTIARTL", "LT",
    "HAL", "BEL", "M&M", "MARUTI", "SUNPHARMA",
    "ITC", "HINDUNILVR", "TITAN", "NTPC", "RVNL", "GOLDBEES",
]

RSS_FEEDS = [
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Livemint Markets", "https://www.livemint.com/rss/markets"),
    ("Livemint Companies", "https://www.livemint.com/rss/companies"),
    ("Moneycontrol Markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
]

SECTOR_KEYWORDS = {
    "Capital Goods": ["defence", "defense", "hal", "bel", "aircraft", "missile", "order"],
    "Financial Services": ["rbi", "repo", "bank", "icici", "hdfc", "sbi", "fii", "fpi", "credit"],
    "Construction": ["capex", "infra", "budget", "metro", "railway", "l&t", "rvnl", "highway"],
    "Power": ["power", "solar", "renewable", "grid", "ntpc", "electricity"],
    "Healthcare": ["pharma", "usfda", "drug", "sun pharma", "healthcare"],
    "Information Technology": ["it ", "tcs", "infosys", "hcl", "software", "ai ", "nasdaq"],
    "Telecommunication": ["telecom", "5g", "airtel", "jio", "arpu"],
    "Automobiles": ["auto", "suv", "ev", "mahindra", "maruti", "crude"],
    "FMCG": ["fmcg", "itc", "hul", "unilever", "rural", "monsoon"],
    "Consumer Services": ["titan", "jewellery", "retail", "consumer"],
    "Gold & Commodities": ["gold", "bullion", "goldbees", "inflation", "silver"],
}


def http_get_text(url, referer="", timeout=25):
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_nse_equity_master():
    master = {}
    try:
        raw = http_get_text(NSE_EQUITY_MASTER_URL, referer="https://www.nseindia.com/")
        reader = csv.DictReader(io.StringIO(raw))
        for row in reader:
            clean = {k.strip().upper(): (v or "").strip() for k, v in row.items() if k}
            sym = clean.get("SYMBOL", "").upper()
            series = clean.get("SERIES", "EQ").upper()
            if not sym or series not in ("EQ", "BE", "BZ", "SM", "ST"):
                continue
            master[sym] = {
                "name": clean.get("NAME OF COMPANY", sym),
                "isin": clean.get("ISIN NUMBER", "").upper(),
                "series": series,
            }
        print(f"Loaded {len(master)} symbols from NSE EQUITY_L.csv")
    except Exception as exc:
        print(f"Warning: could not fetch NSE EQUITY_L.csv: {exc}")
    return master


def fetch_latest_nse_bhavcopy():
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    for days_back in range(0, 7):
        dt = now_ist - timedelta(days=days_back)
        url = NSE_BHAVCOPY_URL_FMT.format(ddmmyyyy=dt.strftime("%d%m%Y"))
        try:
            raw = http_get_text(url, referer="https://www.nseindia.com/")
            reader = csv.DictReader(io.StringIO(raw))
            bhav = {}
            session_date = ""
            for row in reader:
                clean = {k.strip().upper(): (v or "").strip() for k, v in row.items() if k}
                if clean.get("SERIES", "").upper() not in ("EQ", "BE", "BZ", "SM", "ST"):
                    continue
                sym = clean.get("SYMBOL", "").upper()
                if not sym:
                    continue
                if not session_date:
                    session_date = clean.get("DATE1", dt.strftime("%d-%b-%Y"))
                try:
                    close_p = float(clean.get("CLOSE_PRICE", "0") or 0)
                    prev_p = float(clean.get("PREV_CLOSE", "0") or close_p)
                    high_p = float(clean.get("HIGH_PRICE", "0") or close_p)
                    low_p = float(clean.get("LOW_PRICE", "0") or close_p)
                    deliv_str = clean.get("DELIV_PER", "0").replace("-", "").strip()
                    deliv_p = float(deliv_str) if deliv_str else 0.0
                except ValueError:
                    continue
                chg_pct = round(((close_p - prev_p) / prev_p) * 100, 2) if prev_p > 0 else 0.0
                bhav[sym] = {
                    "cmp": round(close_p, 2),
                    "prev": round(prev_p, 2),
                    "chg": chg_pct,
                    "high": round(high_p, 2),
                    "low": round(low_p, 2),
                    "deliv": round(deliv_p, 1),
                }
            if len(bhav) > 100:
                print(f"Loaded {len(bhav)} traded stocks from NSE Bhavcopy ({session_date})")
                return bhav, session_date
        except Exception:
            continue
    print("Warning: could not fetch recent NSE Bhavcopy")
    return {}, ""


def fetch_latest_bse_bhavcopy():
    """Fetches official BSE UDiFF Equity Bhavcopy (all ~4,000+ active BSE listed equities)."""
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    for days_back in range(0, 7):
        dt = now_ist - timedelta(days=days_back)
        url = BSE_UDIFF_BHAVCOPY_URL_FMT.format(yyyymmdd=dt.strftime("%Y%m%d"))
        try:
            raw = http_get_text(url, referer="https://www.bseindia.com/")
            reader = csv.DictReader(io.StringIO(raw))
            bse_rows = []
            session_date = ""
            for row in reader:
                clean = {k.strip().upper(): (v or "").strip() for k, v in row.items() if k}
                # Keep equity instruments (skip debt/debentures when FinInstrmTp is present)
                inst_tp = clean.get("FININSTRMTP", "STK").upper()
                if inst_tp and inst_tp not in ("STK", "EQ", "ETF", ""):
                    continue
                scrip_code = clean.get("FININSTRMID", "").strip()
                tckr = clean.get("TCKRSYMB", "").upper().strip()
                if not scrip_code and not tckr:
                    continue
                if not session_date:
                    session_date = clean.get("TRADD", dt.strftime("%Y-%m-%d"))
                try:
                    close_p = float(clean.get("CLSPRIC", "0") or 0)
                    prev_p = float(clean.get("PRVSCLSGPRIC", "0") or close_p)
                    high_p = float(clean.get("HGHPRIC", "0") or close_p)
                    low_p = float(clean.get("LWPRIC", "0") or close_p)
                except ValueError:
                    continue
                chg_pct = round(((close_p - prev_p) / prev_p) * 100, 2) if prev_p > 0 else 0.0
                bse_rows.append({
                    "sym": tckr or scrip_code,
                    "bse": scrip_code,
                    "name": clean.get("FININSTRMNM", tckr or scrip_code),
                    "isin": clean.get("ISIN", "").upper(),
                    "cmp": round(close_p, 2),
                    "prev": round(prev_p, 2),
                    "chg": chg_pct,
                    "high": round(high_p, 2),
                    "low": round(low_p, 2),
                })
            if len(bse_rows) > 100:
                print(f"Loaded {len(bse_rows)} listed equities from BSE UDiFF Bhavcopy ({session_date})")
                return bse_rows, session_date
        except Exception:
            continue
    print("Warning: could not fetch recent BSE UDiFF Bhavcopy")
    return [], ""


def load_my_holdings():
    try:
        with open("my_holdings.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_yfinance_52w(symbols):
    prices = {}
    for sym in sorted(set(symbols)):
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            hist = ticker.history(period="1y")
            if hist.empty:
                continue
            prices[sym] = {
                "cmp": round(float(hist["Close"].iloc[-1]), 2),
                "high52": round(float(hist["High"].max()), 2),
                "low52": round(float(hist["Low"].min()), 2),
            }
        except Exception as exc:
            print(f"Skipping yfinance {sym}: {exc}")
    return prices


def classify_sectors(text):
    lower = text.lower()
    matched = [sec for sec, words in SECTOR_KEYWORDS.items() if any(w in lower for w in words)]
    return matched[:2] if matched else ["Financial Services"]


def detect_sentiment(text):
    t = (text or "").lower()
    bull = ["surge", "jump", "rally", "gain", "high", "boost", "profit", "order", "approve", "inflow", "buy", "growth", "record", "dividend"]
    bear = ["fall", "drop", "crash", "slump", "selloff", "loss", "tumble", "fear", "outflow", "downgrade", "plunge", "tariff", "inflation"]
    score = sum(1 for w in bull if w in t) - sum(1 for w in bear if w in t)
    return "Bullish" if score > 0 else ("Caution" if score < 0 else "Watch")


def fetch_latest_news():
    items = []
    seen = set()
    for source_name, url in RSS_FEEDS:
        try:
            raw = http_get_text(url, timeout=15)
            root = ET.fromstring(raw)
            for item in root.findall(".//item")[:6]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()[:16]
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                items.append({
                    "title": title,
                    "summary": desc[:240],
                    "source": source_name,
                    "date": pub or "Today",
                    "url": link,
                    "sentiment": detect_sentiment(title + " " + desc),
                    "sectors": classify_sectors(title + " " + desc),
                })
        except Exception as exc:
            print(f"Skipping RSS {source_name}: {exc}")
    return items[:20]


def main():
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")

    nse_master = fetch_nse_equity_master()
    nse_bhav, nse_date = fetch_latest_nse_bhavcopy()
    bse_rows, bse_date = fetch_latest_bse_bhavcopy()

    # Merge NSE + BSE cleanly by ISIN (falling back to symbol when ISIN is absent)
    merged = {}
    for sym in sorted(set(nse_master.keys()) | set(nse_bhav.keys())):
        m = nse_master.get(sym, {})
        b = nse_bhav.get(sym, {})
        isin = m.get("isin", "")
        key = isin or f"NSE:{sym}"
        merged[key] = {
            "sym": sym,
            "bse": "",
            "exch": "NSE",
            "name": m.get("name", sym),
            "cmp": b.get("cmp", 0),
            "prev": b.get("prev", 0),
            "chg": b.get("chg", 0),
            "high": b.get("high", 0),
            "low": b.get("low", 0),
            "deliv": b.get("deliv", 0),
            "isin": isin,
        }

    for row in bse_rows:
        isin = row["isin"]
        key = isin if (isin and isin in merged) else (f"NSE:{row['sym']}" if f"NSE:{row['sym']}" in merged else (isin or f"BSE:{row['bse']}"))
        if key in merged:
            rec = merged[key]
            rec["bse"] = row["bse"]
            rec["exch"] = "NSE+BSE"
            if rec["cmp"] <= 0 and row["cmp"] > 0:
                rec["cmp"] = row["cmp"]
                rec["prev"] = row["prev"]
                rec["chg"] = row["chg"]
                rec["high"] = row["high"]
                rec["low"] = row["low"]
        else:
            merged[key] = {
                "sym": row["sym"],
                "bse": row["bse"],
                "exch": "BSE",
                "name": row["name"],
                "cmp": row["cmp"],
                "prev": row["prev"],
                "chg": row["chg"],
                "high": row["high"],
                "low": row["low"],
                "deliv": 0,
                "isin": isin,
            }

    compact_rows = [
        [
            r["sym"], r["bse"], r["exch"], r["name"],
            r["cmp"], r["prev"], r["chg"], r["high"], r["low"], r["deliv"], r["isin"]
        ]
        for r in sorted(merged.values(), key=lambda x: (x["sym"] or x["bse"]))
    ]

    if compact_rows:
        all_payload = {
            "updated_at_ist": nse_date or bse_date or now_ist,
            "count": len(compact_rows),
            "fields": ["sym", "bse", "exch", "name", "cmp", "prev", "chg", "high", "low", "deliv", "isin"],
            "stocks": compact_rows,
        }
        with open("market_all_stocks.json", "w", encoding="utf-8") as f:
            json.dump(all_payload, f, separators=(",", ":"))
        print(f"Wrote market_all_stocks.json with {len(compact_rows)} merged NSE & BSE listed shares")

    holdings = load_my_holdings()
    holding_syms = [h.get("nse", "").upper() for h in holdings if h.get("nse")]
    featured_prices = fetch_yfinance_52w(DEFAULT_NSE_SYMBOLS + holding_syms)

    for sym in set(DEFAULT_NSE_SYMBOLS + holding_syms):
        if sym not in featured_prices and sym in nse_bhav:
            featured_prices[sym] = {
                "cmp": nse_bhav[sym]["cmp"],
                "high52": nse_bhav[sym]["high"],
                "low52": nse_bhav[sym]["low"],
            }

    daily_payload = {
        "updated_at_ist": now_ist,
        "market_total_listed": len(compact_rows),
        "my_holdings": holdings,
        "prices": featured_prices,
        "news": fetch_latest_news(),
    }
    with open("daily_data.json", "w", encoding="utf-8") as f:
        json.dump(daily_payload, f, indent=2)
    print(f"Wrote daily_data.json with {len(featured_prices)} featured prices and {len(daily_payload['news'])} news items")


if __name__ == "__main__":
    main()
