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
import os
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



def fetch_live_ipos():
    """Fetches current, upcoming, and recently closed NSE/BSE Mainboard & SME IPOs from Zerodha IPO page."""
    import re
    ipos = []
    try:
        raw = http_get_text("https://zerodha.com/ipo/", referer="https://zerodha.com/", timeout=20)
        # Extract table rows from Live, Upcoming, and Closed sections
        sections = [
            ("Open", r'id="live"[\s\S]*?<tbody[^>]*>([\s\S]*?)</tbody>'),
            ("Upcoming", r'id="upcoming"[\s\S]*?<tbody[^>]*>([\s\S]*?)</tbody>'),
            ("Allotment", r'id="closed"[\s\S]*?<tbody[^>]*>([\s\S]*?)</tbody>'),
        ]
        seen = set()
        for status_label, pat in sections:
            m = re.search(pat, raw, re.IGNORECASE)
            if not m:
                continue
            tbody = m.group(1)
            rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", tbody, re.IGNORECASE)
            limit = 8 if status_label != "Allotment" else 4
            for row_html in rows[:limit]:
                clean_text = re.sub(r"<[^>]+>", " | ", row_html)
                parts = [p.strip() for p in re.split(r"\s*\|\s*", clean_text) if p.strip()]
                if len(parts) < 2:
                    continue
                joined = " ".join(parts)
                seg = "SME" if re.search(r"SME", joined) else "Mainboard"
                # Price range extraction (e.g. ₹129 – ₹136 or 129 - 136)
                m_price = re.search(r"(?:₹|Rs\.?\s*)?(\d[\d,]*)\s*[–-]\s*(?:₹|Rs\.?\s*)?(\d[\d,]*)", joined)
                upper_price = int(m_price.group(2).replace(",", "")) if m_price else 0
                price_band = f"₹{m_price.group(1)} – ₹{m_price.group(2)}" if m_price else "TBA"
                # Date range extraction
                m_date = re.search(r"(\d{1,2}(?:st|nd|rd|th)?\s*[A-Za-z]{0,4}\s*[–-]\s*\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3}\s+\d{4})", joined)
                dates_str = m_date.group(1) if m_date else ("Open Now" if status_label == "Open" else "Upcoming")
                # Pick cleanest company name candidate
                name = parts[1] if len(parts) > 1 and len(parts[0]) <= 12 and parts[0].isupper() else parts[0]
                name = re.sub(r"(SME|Mainboard)", "", name).strip()
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())
                verdict = "Check Day-3 QIB & GMP" if status_label == "Open" else ("Wait for Price Band" if upper_price == 0 else "Track Listing")
                tone = "emerald" if status_label == "Open" and seg == "Mainboard" else ("amber" if seg == "SME" else "blue")
                ipos.append({
                    "id": re.sub(r"[^a-z0-9]", "", name.lower())[:20] or f"ipo_{len(ipos)}",
                    "name": name,
                    "segment": seg,
                    "status": status_label,
                    "dates": dates_str,
                    "listingDate": "Check RHP",
                    "priceBand": price_band,
                    "upperPrice": upper_price,
                    "lotSize": 0,
                    "minInv": 0,
                    "issueSizeCr": 0,
                    "gmpRs": 0,
                    "gmpPct": 0,
                    "qibSub": 0,
                    "freshPct": 50,
                    "verdict": verdict,
                    "verdictTone": tone,
                    "reason": f"Auto-synced from Zerodha IPO calendar ({seg} · {status_label}). Enter live GMP & Day-3 QIB in the IPO Analyzer above for a custom score."
                })
        print(f"Fetched {len(ipos)} live/upcoming Indian IPOs")
    except Exception as exc:
        print(f"Skipping IPO fetch: {exc}")
    return ipos



def fetch_market_indices():
    """Fetches Nifty 50, Sensex, Bank Nifty, India VIX, Gold BeES, and USD/INR for top ticker."""
    mapping = [
        ("NIFTY 50", "^NSEI", ""),
        ("SENSEX", "^BSESN", ""),
        ("BANK NIFTY", "^NSEBANK", ""),
        ("INDIA VIX", "^INDIAVIX", ""),
        ("GOLD BeES", "GOLDBEES.NS", "₹"),
        ("USD/INR", "INR=X", "₹"),
    ]
    out = []
    for label, ticker_sym, prefix in mapping:
        try:
            hist = yf.Ticker(ticker_sym).history(period="5d")
            if len(hist) >= 2:
                last_c = float(hist["Close"].iloc[-1])
                prev_c = float(hist["Close"].iloc[-2])
                chg = round(((last_c - prev_c) / prev_c) * 100, 2) if prev_c > 0 else 0.0
                out.append({"name": label, "val": f"{prefix}{last_c:,.2f}", "chg": chg})
        except Exception:
            continue
    return out


def fetch_history_and_corp_actions(symbols):
    """Fetches 1Y monthly/bi-weekly closing history + 50/200-DMA + upcoming dividend/earnings dates."""
    history = {}
    corp_actions = {}
    for sym in sorted(set(symbols)):
        try:
            t = yf.Ticker(f"{sym}.NS")
            hist = t.history(period="1y")
            if not hist.empty and len(hist) >= 20:
                closes = hist["Close"]
                dma50 = round(float(closes.tail(50).mean()), 2)
                dma200 = round(float(closes.tail(200).mean()), 2)
                sampled = closes.iloc[::max(1, len(closes) // 18)]
                history[sym] = {
                    "dates": [d.strftime("%d %b") for d in sampled.index],
                    "closes": [round(float(v), 2) for v in sampled.values],
                    "dma50": dma50,
                    "dma200": dma200,
                }
            # Try extracting next earnings/ex-dividend date cleanly
            try:
                cal = t.calendar
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    exd = cal.get("Ex-Dividend Date")
                    parts = []
                    if exd:
                        parts.append(f"Ex-Div: {str(exd)[:10]}")
                    if ed:
                        first_ed = ed[0] if isinstance(ed, list) and ed else ed
                        parts.append(f"Results: {str(first_ed)[:10]}")
                    if parts:
                        corp_actions[sym] = " · ".join(parts)
            except Exception:
                pass
        except Exception:
            continue
    return history, corp_actions



def fetch_upcoming_dividends():
    """Fetches upcoming NSE equity dividend corporate actions for the Dividends Calendar."""
    import re as _re
    results = []
    try:
        cj = urllib.request.HTTPCookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
            ("Accept", "application/json, text/plain, */*"),
            ("Referer", "https://www.nseindia.com/companies-listing/corporate-filings-actions"),
        ]
        # Warm session cookie
        try:
            opener.open("https://www.nseindia.com", timeout=8).read(256)
        except Exception:
            pass
        resp = opener.open("https://www.nseindia.com/api/corporates-corporateActions?index=equities", timeout=12)
        rows = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if isinstance(rows, list):
            for r in rows:
                subj = str(r.get("subject") or "")
                if "dividend" not in subj.lower():
                    continue
                m = _re.search(r"(?:rs\.?|inr|₹)\s*([0-9]+(?:\.[0-9]+)?)", subj, _re.I)
                amt = float(m.group(1)) if m else 0.0
                div_type = "Interim" if "interim" in subj.lower() else ("Special" if "special" in subj.lower() else "Final")
                results.append({
                    "sym": str(r.get("symbol") or "").strip().upper(),
                    "bse": "",
                    "name": str(r.get("comp") or r.get("symbol") or "").strip(),
                    "type": div_type,
                    "amount": amt,
                    "exDate": str(r.get("exDate") or "").strip(),
                    "recordDate": str(r.get("recDate") or r.get("exDate") or "").strip(),
                    "status": "Upcoming",
                    "note": subj[:90],
                })
                if len(results) >= 20:
                    break
    except Exception as e:
        print(f"Skipping NSE live dividends fetch: {e}")
    return results


def fetch_fii_dii_latest():
    """Fetches the latest NSE provisional FII/DII cash market net flow (₹ Cr)."""
    try:
        cj = urllib.request.HTTPCookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
            ("Accept", "application/json, text/plain, */*"),
            ("Referer", "https://www.nseindia.com/reports/fii-dii"),
        ]
        try:
            opener.open("https://www.nseindia.com", timeout=8).read(256)
        except Exception:
            pass
        resp = opener.open("https://www.nseindia.com/api/fiidiiTradeReact", timeout=12)
        rows = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if isinstance(rows, list) and rows:
            fii_net, dii_net, dt_str = 0.0, 0.0, ""
            for r in rows:
                cat = str(r.get("category") or "").upper()
                net = float(str(r.get("netValue") or "0").replace(",", ""))
                if not dt_str and r.get("date"):
                    dt_str = str(r.get("date"))[:6]
                if "FII" in cat or "FPI" in cat:
                    fii_net = round(net, 2)
                elif "DII" in cat:
                    dii_net = round(net, 2)
            if dt_str:
                return {"date": dt_str.replace("-", " "), "fii": fii_net, "dii": dii_net}
    except Exception as e:
        print(f"Skipping NSE live FII/DII fetch: {e}")
    return None


def fetch_daily_sector_pulse():
    """Fetches daily % change for major NSE sectoral indices via yfinance."""
    specs = [
        ("^NSEBANK", "Financial Services", "Nifty Bank"),
        ("^CNXIT", "Information Technology", "Nifty IT"),
        ("^CNXAUTO", "Automobiles", "Nifty Auto"),
        ("^CNXPHARMA", "Healthcare", "Nifty Pharma"),
        ("^CNXFMCG", "FMCG", "Nifty FMCG"),
        ("^CNXENERGY", "Oil & Gas", "Nifty Energy"),
        ("^CNXINFRA", "Construction", "Nifty Infra"),
        ("^CNXCONSUM", "Consumer Services", "Nifty Consumption"),
    ]
    out = []
    for sym, sec, label in specs:
        try:
            hist = yf.Ticker(sym).history(period="5d")
            if hist is not None and len(hist) >= 2:
                prev_c = float(hist["Close"].iloc[-2])
                curr_c = float(hist["Close"].iloc[-1])
                chg = round(((curr_c - prev_c) / prev_c) * 100, 2) if prev_c > 0 else 0.0
                out.append({
                    "sector": sec,
                    "displayLabel": label,
                    "chg": chg,
                    "note": f"Close {curr_c:,.2f} · Auto-synced via GitHub Action",
                })
        except Exception:
            continue
    return out



def load_alert_config():
    """Loads alert_stocks.json specifying Market Mood & per-share alert rules."""
    default_cfg = {
        "market_mood_enabled": True,
        "move_threshold_pct": 2.0,
        "app_url": "https://vikas030797-beep.github.io/BharatAlpha/",
        "stocks": [],
    }
    try:
        if os.path.exists("alert_stocks.json"):
            with open("alert_stocks.json", "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                default_cfg.update(raw)
    except Exception as e:
        print(f"Skipping alert_stocks.json load: {e}")
    return default_cfg


def _safe_float(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("₹", "").replace("%", "").strip())
    except Exception:
        return default


def compute_market_mood(indices, featured_prices):
    """Computes a 0-100 Market Mood index from Nifty 50, Bank Nifty, India VIX & breadth."""
    score = 50.0
    nifty_chg = 0.0
    bank_chg = 0.0
    vix_val = 14.5
    for idx in (indices or []):
        name = str(idx.get("name") or "").upper()
        chg = _safe_float(idx.get("chg"), 0.0)
        val = _safe_float(idx.get("val"), 0.0)
        if "NIFTY 50" in name:
            nifty_chg = chg
        elif "BANK" in name:
            bank_chg = chg
        elif "VIX" in name and val > 0:
            vix_val = val

    score += max(-22.0, min(22.0, nifty_chg * 11.0))
    score += max(-12.0, min(12.0, bank_chg * 5.0))
    if vix_val <= 13.0:
        score += 8.0
    elif vix_val >= 20.0:
        score -= 14.0
    elif vix_val >= 16.5:
        score -= 7.0

    # Breadth boost from tracked stocks' day change if present
    up_cnt, tot_cnt = 0, 0
    for p in (featured_prices or {}).values():
        if isinstance(p, dict) and p.get("chg") is not None:
            tot_cnt += 1
            if float(p.get("chg") or 0) > 0:
                up_cnt += 1
    if tot_cnt >= 5:
        breadth_ratio = up_cnt / tot_cnt
        score += (breadth_ratio - 0.5) * 18.0

    score = int(round(max(5.0, min(95.0, score))))
    if score >= 75:
        label = "Extreme Greed"
    elif score >= 60:
        label = "Greed"
    elif score >= 45:
        label = "Neutral"
    elif score >= 30:
        label = "Fear"
    else:
        label = "Extreme Fear"

    return {
        "score": score,
        "label": label,
        "nifty_chg": round(nifty_chg, 2),
        "bank_chg": round(bank_chg, 2),
        "vix": round(vix_val, 2),
    }


def fetch_intraday_day_changes(symbols):
    """Fetches latest price & day % change for alert symbols via yfinance."""
    out = {}
    for sym in sorted(set(s.upper() for s in symbols if s)):
        try:
            hist = yf.Ticker(f"{sym}.NS").history(period="5d")
            if hist is not None and len(hist) >= 2:
                prev_c = float(hist["Close"].iloc[-2])
                curr_c = float(hist["Close"].iloc[-1])
                open_c = float(hist["Open"].iloc[-1])
                high_c = float(hist["High"].iloc[-1])
                low_c = float(hist["Low"].iloc[-1])
                chg = round(((curr_c - prev_c) / prev_c) * 100, 2) if prev_c > 0 else 0.0
                out[sym] = {
                    "cmp": round(curr_c, 2),
                    "prev": round(prev_c, 2),
                    "open": round(open_c, 2),
                    "high": round(high_c, 2),
                    "low": round(low_c, 2),
                    "chg": chg,
                }
        except Exception:
            continue
    return out


def build_notification_payload(alert_cfg, market_mood, indices, featured_prices, alert_quotes, fii_dii_latest, now_ist):
    """Builds concise title + body for Market Mood and user-selected share alerts."""
    mood_score = market_mood.get("score", 50)
    mood_label = market_mood.get("label", "Neutral")
    time_short = now_ist.split(",")[1].strip() if "," in now_ist else now_ist
    title = f"BharatAlpha · {mood_label} ({mood_score}/100)"

    lines = []
    if alert_cfg.get("market_mood_enabled", True):
        nifty_str, bank_str = "", ""
        for idx in (indices or []):
            nm = str(idx.get("name") or "").upper()
            v = _safe_float(idx.get("val"), 0.0)
            c = _safe_float(idx.get("chg"), 0.0)
            sign = "+" if c >= 0 else ""
            if "NIFTY 50" in nm:
                nifty_str = f"Nifty {v:,.0f} ({sign}{c:.2f}%)"
            elif "BANK" in nm:
                bank_str = f"BankNifty {v:,.0f} ({sign}{c:.2f}%)"
        if nifty_str or bank_str:
            lines.append(" · ".join([x for x in [nifty_str, bank_str] if x]))
        if fii_dii_latest and fii_dii_latest.get("date"):
            f_val = float(fii_dii_latest.get("fii") or 0)
            d_val = float(fii_dii_latest.get("dii") or 0)
            fs = "+" if f_val >= 0 else ""
            ds = "+" if d_val >= 0 else ""
            lines.append(f"Flows ({fii_dii_latest['date']}): FII {fs}₹{f_val:,.0f}Cr · DII {ds}₹{d_val:,.0f}Cr")

    threshold = float(alert_cfg.get("move_threshold_pct") or 2.0)
    triggered = []
    regular = []
    for item in (alert_cfg.get("stocks") or []):
        sym = str(item.get("sym") or "").upper().strip()
        if not sym:
            continue
        q = alert_quotes.get(sym) or {}
        fp = featured_prices.get(sym) or {}
        cmp_v = float(q.get("cmp") or fp.get("cmp") or 0)
        if cmp_v <= 0:
            continue
        chg = float(q.get("chg") or 0)
        sign = "+" if chg >= 0 else ""
        target = float(item.get("target") or 0)
        sl = float(item.get("stop_loss") or 0)
        high52 = float(fp.get("high52") or 0)
        low52 = float(fp.get("low52") or 0)

        tags = []
        if target > 0 and cmp_v >= target:
            tags.append(f"🎯 Target ₹{target:,.0f}")
        if sl > 0 and cmp_v <= sl:
            tags.append(f"🛑 SL ₹{sl:,.0f}")
        if abs(chg) >= threshold:
            tags.append("🚀 Surge" if chg > 0 else "⚠️ Drop")
        if high52 > 0 and cmp_v >= high52 * 0.985:
            tags.append("Near 52W High")
        elif low52 > 0 and cmp_v <= low52 * 1.015:
            tags.append("Near 52W Low")

        entry = f"{sym} ₹{cmp_v:,.2f} ({sign}{chg:.2f}%)"
        if tags:
            triggered.append(f"• {entry} — {', '.join(tags)}")
        else:
            regular.append(f"{sym} {sign}{chg:.1f}%")

    if triggered:
        lines.append("Alerts:")
        lines.extend(triggered[:5])
    elif regular:
        lines.append("Watch: " + " · ".join(regular[:5]))

    if not lines:
        return None, None
    lines.append(f"Updated {time_short}")
    return title, "\n".join(lines)


def dispatch_push_notifications(title, body, app_url):
    """Dispatches push alerts via OneSignal, Telegram, and/or ntfy if secrets are set."""
    if not title or not body:
        return

    # 1. OneSignal Native PWA Push (shows BharatAlpha icon on iPhone)
    os_app_id = os.environ.get("ONESIGNAL_APP_ID", "").strip()
    os_api_key = os.environ.get("ONESIGNAL_REST_API_KEY", "").strip()
    if os_app_id and os_api_key:
        try:
            payload = json.dumps({
                "app_id": os_app_id,
                "included_segments": ["Subscribed Users"],
                "headings": {"en": title},
                "contents": {"en": body},
                "url": app_url,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.onesignal.com/notifications",
                data=payload,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Authorization": f"Key {os_api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                print(f"OneSignal push sent: HTTP {resp.status}")
        except Exception as e:
            print(f"OneSignal push skipped/failed: {e}")

    # 2. Telegram Bot Push (instant rock-solid iPhone delivery)
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        try:
            tg_text = f"*{title}*\n{body}\n\n[Open BharatAlpha]({app_url})"
            payload = json.dumps({
                "chat_id": tg_chat,
                "text": tg_text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                print(f"Telegram push sent: HTTP {resp.status}")
        except Exception as e:
            print(f"Telegram push skipped/failed: {e}")

    # 3. ntfy.sh Push (zero-account topic)
    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()
    if ntfy_topic:
        try:
            url = ntfy_topic if ntfy_topic.startswith("http") else f"https://ntfy.sh/{ntfy_topic}"
            req = urllib.request.Request(
                url,
                data=body.encode("utf-8"),
                headers={
                    "Title": title,
                    "Click": app_url,
                    "Tags": "chart_with_upwards_trend",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                print(f"ntfy push sent: HTTP {resp.status}")
        except Exception as e:
            print(f"ntfy push skipped/failed: {e}")


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
    alert_cfg = load_alert_config()
    alert_syms = [str(s.get("sym") or "").upper() for s in (alert_cfg.get("stocks") or []) if s.get("sym")]
    all_tracked = list(dict.fromkeys(DEFAULT_NSE_SYMBOLS + holding_syms + alert_syms))
    featured_prices = fetch_yfinance_52w(all_tracked)
    alert_quotes = fetch_intraday_day_changes(alert_syms or DEFAULT_NSE_SYMBOLS[:10])

    for sym in all_tracked:
        if sym not in featured_prices and sym in nse_bhav:
            featured_prices[sym] = {
                "cmp": nse_bhav[sym]["cmp"],
                "high52": nse_bhav[sym]["high"],
                "low52": nse_bhav[sym]["low"],
            }
        if sym in featured_prices and sym in alert_quotes:
            featured_prices[sym]["chg"] = alert_quotes[sym]["chg"]

    hist_data, corp_data = fetch_history_and_corp_actions(DEFAULT_NSE_SYMBOLS)
    indices = fetch_market_indices()
    fii_dii_latest = fetch_fii_dii_latest()
    market_mood = compute_market_mood(indices, featured_prices)

    daily_payload = {
        "updated_at_ist": now_ist,
        "market_total_listed": len(compact_rows),
        "my_holdings": holdings,
        "prices": featured_prices,
        "news": fetch_latest_news(),
        "ipos": fetch_live_ipos(),
        "indices": indices,
        "history": hist_data,
        "corp_actions": corp_data,
        "dividends": fetch_upcoming_dividends(),
        "fii_dii_latest": fii_dii_latest,
        "sector_pulse_daily": fetch_daily_sector_pulse(),
        "market_mood": market_mood,
    }
    with open("daily_data.json", "w", encoding="utf-8") as f:
        json.dump(daily_payload, f, indent=2)
    print(f"Wrote daily_data.json with {len(featured_prices)} featured prices, Market Mood {market_mood['score']}/100 ({market_mood['label']})")

    notif_title, notif_body = build_notification_payload(
        alert_cfg, market_mood, indices, featured_prices, alert_quotes, fii_dii_latest, now_ist
    )
    if notif_title and notif_body:
        dispatch_push_notifications(
            notif_title,
            notif_body,
            alert_cfg.get("app_url") or "https://vikas030797-beep.github.io/BharatAlpha/",
        )


if __name__ == "__main__":
    main()
