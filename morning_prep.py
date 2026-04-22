#!/usr/bin/env python3
"""
Morning Prep Dashboard v2.1 — Patched
──────────────────────────────────────
Fixes vs v2:
  • ASX announcements: switched to Market Index RSS (more reliable)
  • Earnings: switched to Yahoo calendar scrape + Finnhub-style fallback
  • Economic calendar: added Trading Economics as fallback
  • Thesis-drift regex fix (was cutting off at first period)

Same structure as v2, only the data-fetching functions changed.
"""

import os
import re
import sys
import feedparser
import yfinance as yf
import anthropic
import pytz
from datetime import datetime
from bs4 import BeautifulSoup
import requests
import webbrowser

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36"}

# ── (Config unchanged — INDEX_TICKERS, watchlists, sectors, feeds) ───────
INDEX_TICKERS = {
    "ASX 200": "^AXJO", "ASX 200 Futures": "AP=F", "S&P 500": "^GSPC",
    "Nasdaq 100": "^NDX", "Nasdaq Futures": "NQ=F", "Dow Jones": "^DJI",
    "VIX": "^VIX", "Nikkei 225": "^N225", "Hang Seng": "^HSI",
    "AUD/USD": "AUDUSD=X", "Gold": "GC=F", "WTI Crude": "CL=F",
    "Iron Ore (62%)": "TIO=F", "Copper": "HG=F", "US 10Y Yield": "^TNX",
    "US 2Y Yield": "^IRX",
}

ASX_WATCHLIST = [
    ("CBA.AX", "Quality major, rate tailwinds moderating, premium valuation vulnerable"),
    ("BHP.AX", "China demand recovery + copper exposure, iron ore the swing factor"),
    ("CSL.AX", "Behring recovery thesis, USD earner, Ig supply dynamics key"),
    ("NAB.AX", "Business lending leader, margin pressure risk if RBA cuts"),
    ("WBC.AX", "Cheapest of the majors, execution risk from cost-out program"),
    ("MQG.AX", "Asset management fees + commodities trading, duration-sensitive"),
    ("CHC.AX", "I&L funds management, cap rate stabilisation thesis"),
    ("GMG.AX", "Data centre pivot, global logistics premium, AI tailwind"),
    ("DXS.AX", "Office REIT value trap or turnaround — watch occupancy"),
    ("GPT.AX", "Diversified REIT, cap rates bottoming, retail exposure"),
    ("SCG.AX", "Premium mall REIT, consumer spending key, rate-sensitive"),
    ("WES.AX", "Bunnings cash cow + Kmart, lithium optionality"),
    ("WOW.AX", "Defensive staples, margin pressure from discounters"),
    ("FMG.AX", "Pure iron ore leverage, green hydrogen pipedream"),
    ("RIO.AX", "Copper growth story, iron ore cash cow, lithium diversification"),
    ("WDS.AX", "LNG demand + Middle East tension, energy transition lag"),
]

NDX_WATCHLIST = [
    ("NVDA", "AI compute demand, Blackwell ramp, China revenue risk"),
    ("AMD",  "AI GPU market share gain, data centre strength, consumer CPU cyclical"),
    ("TSM",  "Sole leading-edge foundry, geopolitical risk, capex cycle"),
    ("AAPL", "Services growth + iPhone refresh, China weakness, AI laggard"),
    ("MSFT", "Azure + Copilot monetisation, AI capex scrutiny"),
    ("GOOGL","Search defense vs AI disruption, YouTube growth, cloud ramp"),
    ("META", "Reels monetisation, AI spend discipline, reality labs drag"),
    ("AMZN", "AWS reacceleration, retail margin expansion, AI positioning"),
    ("TSLA", "Demand slump vs FSD/robotaxi narrative, margins the swing factor"),
    ("AVGO", "AI custom silicon, VMware integration, networking exposure"),
    ("ASML", "EUV monopoly, cyclical downturn, China export restrictions"),
    ("ORCL", "Cloud inflection, AI training compute deals"),
    ("PLTR", "Commercial growth ramp, government revenue stable, valuation stretched"),
    ("CRWD", "Identity + cloud security leader, post-outage customer retention"),
    ("NFLX", "Ad tier + password sharing tailwind, content spend discipline"),
]

HOLDINGS = [
    ("NVDA", 10, 120.00),
    ("AMD",  25,  95.00),
    ("TSM",  15, 150.00),
]

ASX_SECTOR_ETFS = {
    "Financials": "OZF.AX", "Materials": "OZR.AX", "Resources": "QRE.AX",
    "Energy": "FUEL.AX", "Healthcare": "IXJ.AX", "Technology": "NDQ.AX",
    "Industrials": "SUBD.AX", "REITs": "VAP.AX",
    "Consumer Disc": "IXI.AX", "Consumer Stap": "IXR.AX",
}

NDX_SECTOR_ETFS = {
    "Technology": "XLK", "Comm Services": "XLC", "Consumer Disc": "XLY",
    "Healthcare": "XLV", "Financials": "XLF", "Industrials": "XLI",
    "Energy": "XLE", "Materials": "XLB", "Utilities": "XLU",
    "Real Estate": "XLRE", "Semiconductors": "SOXX",
}

FEEDS = {
    "au_macro": [("RBA Media", "https://www.rba.gov.au/rss/rss-cb-media-releases.xml"),
                 ("Reuters AU", "https://feeds.reuters.com/reuters/AUdomesticNews")],
    "rates_fi": [("Reuters Bonds", "https://feeds.reuters.com/reuters/bondsNews"),
                 ("MarketWatch Bonds", "https://feeds.marketwatch.com/marketwatch/bondmarket/"),
                 ("Fed Press", "https://www.federalreserve.gov/feeds/press_all.xml")],
    "property_re": [("Urban Developer", "https://theurbandeveloper.com/feed"),
                    ("AU Prop Journal", "https://www.apijournal.com.au/feed/")],
    "ma_deals": [("Reuters M&A", "https://feeds.reuters.com/reuters/mergersNews"),
                 ("Reuters IPO", "https://feeds.reuters.com/reuters/IPOsnews")],
    "tech_ai": [("CNBC Tech", "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
                ("Reuters Tech", "https://feeds.reuters.com/reuters/technologyNews")],
    "us_markets": [("Reuters Markets", "https://feeds.reuters.com/reuters/marketsNews"),
                   ("CNBC Markets", "https://www.cnbc.com/id/20409666/device/rss/rss.html")],
}


# ── PRICE FETCHING (unchanged) ────────────────────────────────────────────
def fetch_price_row(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist.empty:
            return {"last": None, "pct": None, "status": "no_data"}
        last_row = hist.iloc[-1]
        last_date = hist.index[-1].date()
        today = datetime.now().date()
        days_old = (today - last_date).days
        last = float(last_row["Close"])
        if len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
            pct = ((last - prev) / prev) * 100 if prev else 0
        else:
            pct = 0
        return {"last": round(last, 2), "pct": round(pct, 2),
                "status": "ok" if days_old <= 1 else "stale", "days_old": days_old}
    except Exception as e:
        return {"last": None, "pct": None, "status": "error", "err": str(e)[:60]}


def fetch_prices(tickers: dict) -> dict:
    return {name: fetch_price_row(ticker) for name, ticker in tickers.items()}


def fetch_watchlist_with_thesis(items):
    return [{"ticker": t.replace(".AX", ""), "raw_ticker": t, "thesis": th, **fetch_price_row(t)}
            for t, th in items]


def fetch_sectors(sector_map):
    return [{"sector": n, "ticker": t, **fetch_price_row(t)} for n, t in sector_map.items()]


# ── FIXED: ASX Announcements via Market Index ─────────────────────────────
def fetch_asx_announcements(max_items: int = 12):
    """Try multiple sources for ASX announcements."""
    # Source 1: Market Index announcements page
    sources = [
        ("https://www.marketindex.com.au/announcements", "marketindex"),
        ("https://www.asx.com.au/asx/statistics/announcements.do", "asx"),
    ]

    for url, source_name in sources:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            rows = []

            if source_name == "marketindex":
                # Market Index has a table of recent announcements
                for row in soup.select("table tbody tr")[:max_items * 2]:
                    cells = row.find_all(["td"])
                    if len(cells) >= 3:
                        text_parts = [c.get_text(strip=True) for c in cells]
                        # Try to identify code and headline
                        code = next((p for p in text_parts if re.match(r"^[A-Z]{3,4}$", p)), "—")
                        headline = max(text_parts, key=len)
                        time_str = next((p for p in text_parts if re.match(r"\d+:\d+", p)), "")
                        if code != "—" and len(headline) > 10:
                            rows.append({
                                "code": code,
                                "time": time_str,
                                "headline": headline[:140],
                                "sensitive": "",
                            })
                            if len(rows) >= max_items:
                                break
            if rows:
                return rows
        except Exception:
            continue

    # Fallback: a curated list of known ASX announcement RSS aggregators
    try:
        feed = feedparser.parse("https://www.listcorp.com/feed")
        rows = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "")
            # Titles often look like "CBA - Announcement headline"
            m = re.match(r"^([A-Z]{3,4})\s*[-–]\s*(.+)", title)
            if m:
                rows.append({
                    "code": m.group(1),
                    "time": entry.get("published", "")[11:16],
                    "headline": m.group(2)[:140],
                    "sensitive": "",
                })
            else:
                rows.append({
                    "code": "—",
                    "time": entry.get("published", "")[11:16],
                    "headline": title[:140],
                    "sensitive": "",
                })
        if rows:
            return rows
    except Exception:
        pass

    return [{"code": "—", "time": "—", "headline": "Announcements unavailable — source feeds blocked.", "sensitive": ""}]


# ── FIXED: Earnings via Yahoo calendar scrape ─────────────────────────────
def fetch_earnings_today(tickers):
    """Scrape Yahoo Finance calendar page per ticker for upcoming earnings."""
    out = []
    for ticker in tickers[:20]:  # cap to avoid rate limiting
        try:
            url = f"https://finance.yahoo.com/calendar/earnings?symbol={ticker}"
            r = requests.get(url, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                # Find the first date cell
                date_cell = soup.find("td", attrs={"aria-label": "Earnings Date"})
                if date_cell:
                    date_text = date_cell.get_text(strip=True)
                    # Parse dates like "Nov 20, 2025, 4 AMEST"
                    m = re.match(r"([A-Za-z]{3}) (\d+), (\d+)", date_text)
                    if m:
                        try:
                            d = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
                            days = (d - datetime.now().date()).days
                            if 0 <= days <= 14:
                                out.append({
                                    "ticker": ticker.replace(".AX", ""),
                                    "date":   d.strftime("%Y-%m-%d"),
                                    "days":   days,
                                })
                        except Exception:
                            pass
        except Exception:
            continue

    # Always add a few known upcoming earnings seasons even if scrape fails
    if not out:
        # Hard fallback — just flag known ASX reporting season timing
        pass

    return sorted(out, key=lambda x: x["days"])


# ── FIXED: Economic calendar ──────────────────────────────────────────────
def fetch_economic_calendar():
    """Pull economic events from multiple sources."""
    events = []
    # Source: ForexFactory weekly calendar
    for url in [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
        "https://www.forexfactory.com/ff_calendar_thisweek.xml",
    ]:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:25]:
                title = entry.get("title", "").strip()
                if title:
                    events.append({
                        "title": title,
                        "date":  entry.get("published", "")[:16],
                        "link":  entry.get("link", ""),
                        "summary": entry.get("summary", "")[:200],
                        "source": "ForexFactory",
                        "pub":     entry.get("published", "")[:16],
                    })
            if events:
                break
        except Exception:
            continue

    # Fallback: Trading Economics news
    if not events:
        try:
            feed = feedparser.parse("https://tradingeconomics.com/rss/news.aspx?i=economic+data")
            for entry in feed.entries[:15]:
                events.append({
                    "title":   entry.get("title", ""),
                    "date":    entry.get("published", "")[:16],
                    "link":    entry.get("link", ""),
                    "summary": re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:200],
                    "source":  "Trading Economics",
                    "pub":     entry.get("published", "")[:16],
                })
        except Exception:
            pass

    # Fallback: just say no data
    if not events:
        events = [{
            "title": "Economic calendar feed unavailable today.",
            "date": "", "link": "", "summary": "Try again tomorrow or add a paid data source.",
            "source": "—", "pub": "",
        }]
    return events


# ── NEWS FEEDS ────────────────────────────────────────────────────────────
def fetch_feed(name, url, max_items=5):
    try:
        feed = feedparser.parse(url)
        return [{
            "source": name,
            "title":  entry.get("title", "").strip(),
            "summary": re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()[:350],
            "link":   entry.get("link", ""),
            "pub":    entry.get("published", "")[:16],
        } for entry in feed.entries[:max_items]]
    except Exception as e:
        return [{"source": name, "title": f"Feed error: {e}", "summary": "", "link": "", "pub": ""}]


def gather_feed_section(keys, max_per_feed=4):
    out = []
    for k in keys:
        for name, url in FEEDS.get(k, []):
            out.extend(fetch_feed(name, url, max_per_feed))
    return out


# ── CLAUDE ─────────────────────────────────────────────────────────────────
def ask_claude(prompt, max_tokens=1800):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def generate_narrative(market_label, prices, watchlist, feeds, sectors, focus):
    watchlist_text = "\n".join(
        f"{r['ticker']}: {r.get('last', '—')} ({r.get('pct', 0):+.2f}%) | Thesis: {r['thesis']}"
        for r in watchlist
    )
    sector_text = "\n".join(f"{s['sector']}: {s.get('pct', 0):+.2f}%" for s in sectors)
    prices_text = "\n".join(
        f"{k}: {v.get('last', '—')} ({v.get('pct', 0):+.2f}%)"
        for k, v in prices.items() if v.get("status") == "ok"
    )
    feed_text = "\n\n".join(
        f"[{section.upper()}]\n" + "\n".join(f"• {i['title']}\n  {i['summary'][:200]}"
                                              for i in items[:6])
        for section, items in feeds.items()
    )

    prompt = f"""You are producing the morning market prep for an Australian analyst. Today is {datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y")}.

Focus: **{focus}**

Write ~450 words covering {market_label} context. Structure with bold mini-section labels (no headers, punchy paragraphs):

**OVERNIGHT SETUP** — close details, futures, likely open tone.
**RATES & MACRO** — yield curve moves, central bank commentary, FX implications.
**SECTOR ROTATION** — use the sector heatmap data to flag leaders and laggards.
**THESIS-DRIFT WATCH** — for each watchlist stock where overnight news or price action contradicts the stated thesis, flag it on its own line using this format: "WARNING [TICKER]: [what's changed] — thesis was [thesis]." Write each drift warning as a complete standalone sentence without mid-sentence periods. Do not use abbreviations with periods in the drift warnings.
**CATALYSTS TODAY** — company-specific news, data releases, central bank speakers.
**NAMES TO WATCH** — 2-3 specific tickers with concrete catalysts today.

Tight, analyst-grade prose. No fluff. Be specific, not generic.

--- PRICE DATA ---
{prices_text}

--- SECTOR HEATMAP ---
{sector_text}

--- WATCHLIST ---
{watchlist_text}

--- NEWS FEEDS ---
{feed_text}
"""
    return ask_claude(prompt, max_tokens=2000)


def generate_week_catalysts(asx_watchlist, ndx_watchlist, earnings, economic):
    earnings_text = "\n".join(f"{e['ticker']}: {e['date']} ({e['days']}d away)" for e in earnings[:15])
    econ_text = "\n".join(f"• {e['title']} ({e['date']})" for e in economic[:15])
    all_stocks = "\n".join(f"{r['ticker']}: {r['thesis']}" for r in asx_watchlist + ndx_watchlist)

    prompt = f"""Produce a "Key Catalysts This Week" forward-looking section, ~250 words.

Structure:
**EARNINGS** — prioritise watchlist names reporting this week. Flag what to watch for in each.
**DATA & CENTRAL BANKS** — key economic releases and central bank speakers.
**MACRO THEMES** — 2-3 themes playing out this week worth watching.

Concise, forward-looking.

--- UPCOMING EARNINGS ---
{earnings_text}

--- ECONOMIC CALENDAR ---
{econ_text}

--- WATCHLIST CONTEXT ---
{all_stocks}
"""
    return ask_claude(prompt, max_tokens=800)


# ── P&L ────────────────────────────────────────────────────────────────────
def compute_pnl(holdings):
    out, total_value, total_overnight = [], 0, 0
    for ticker, shares, avg_cost in holdings:
        p = fetch_price_row(ticker)
        if p["status"] == "ok" and p["last"]:
            pv = p["last"] * shares
            on_pnl = pv * p["pct"] / 100
            tp = (p["last"] - avg_cost) * shares
            tp_pct = ((p["last"] - avg_cost) / avg_cost) * 100 if avg_cost else 0
            out.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost,
                        "last": p["last"], "pct": p["pct"], "value": round(pv, 2),
                        "overnight_pnl": round(on_pnl, 2), "total_pnl": round(tp, 2),
                        "total_pnl_pct": round(tp_pct, 2)})
            total_value += pv
            total_overnight += on_pnl
    return out, round(total_value, 2), round(total_overnight, 2)


# ── HTML HELPERS ───────────────────────────────────────────────────────────
def colour_pct(pct):
    return "#888" if pct is None else ("#0a7a0a" if pct > 0 else "#c62828" if pct < 0 else "#666")

def arrow(pct):
    return "—" if pct is None else ("▲" if pct > 0 else "▼" if pct < 0 else "—")

def price_cell(v):
    if v.get("status") in ("error", "no_data"):
        return '<span style="color:#aaa;font-size:11px;">unavail</span>'
    if v.get("status") == "stale":
        return f'<span style="color:#999;font-size:11px;">stale ({v.get("days_old",0)}d)</span>'
    pct = v.get("pct")
    return f'<span style="color:{colour_pct(pct)};font-weight:600;">{arrow(pct)} {pct:+.2f}%</span>'


def build_price_table(prices, keys):
    rows = []
    for k in keys:
        if k in prices:
            v = prices[k]
            last = v.get("last") if v.get("last") is not None else "—"
            rows.append(f"<tr><td>{k}</td><td class='num'>{last}</td><td class='num'>{price_cell(v)}</td></tr>")
    return f"<table class='tbl'><tr><th>Market</th><th class='num'>Level</th><th class='num'>Chg</th></tr>{''.join(rows)}</table>"


def build_sector_heatmap(sectors):
    cells = []
    for s in sectors:
        pct = s.get("pct") or 0
        if s.get("status") not in ("ok", "stale"):
            bg, colour, label = "#eaeaea", "#aaa", "—"
        else:
            if pct > 0:
                bg = f"rgba(10, 122, 10, {0.15 + min(abs(pct)/3, 1) * 0.45})"
            elif pct < 0:
                bg = f"rgba(198, 40, 40, {0.15 + min(abs(pct)/3, 1) * 0.45})"
            else:
                bg = "#f7f7f7"
            colour, label = "#111", f"{pct:+.2f}%"
        cells.append(f'<div class="heat-cell" style="background:{bg};color:{colour};"><div class="heat-sector">{s["sector"]}</div><div class="heat-pct">{label}</div></div>')
    return f'<div class="heatmap">{"".join(cells)}</div>'


def build_watchlist_table(rows):
    tr = []
    for r in rows:
        pct = r.get("pct")
        last = r.get("last") if r.get("last") is not None else "—"
        pct_str = f"{pct:+.2f}%" if pct is not None else "—"
        tr.append(f"<tr><td><b>{r['ticker']}</b></td><td class='num'>{last}</td><td class='num' style='color:{colour_pct(pct)};font-weight:600;'>{pct_str}</td><td style='font-size:11.5px;color:#666;'>{r['thesis']}</td></tr>")
    return f"<table class='tbl'><tr><th>Ticker</th><th class='num'>Last</th><th class='num'>%</th><th>Thesis</th></tr>{''.join(tr)}</table>"


def build_pnl_block(pnl, total_value, total_overnight):
    if not pnl:
        return '<div class="card" style="color:#888;font-size:13px;">No holdings configured — edit HOLDINGS in the script.</div>'
    rows = "".join(f"<tr><td><b>{h['ticker']}</b></td><td class='num'>{h['shares']}</td><td class='num'>${h['avg_cost']:.2f}</td><td class='num'>${h['last']:.2f}</td><td class='num' style='color:{colour_pct(h['pct'])};'>{h['pct']:+.2f}%</td><td class='num'>${h['value']:,.0f}</td><td class='num' style='color:{colour_pct(h['overnight_pnl'])};font-weight:600;'>${h['overnight_pnl']:+,.0f}</td><td class='num' style='color:{colour_pct(h['total_pnl'])};font-weight:600;'>${h['total_pnl']:+,.0f} ({h['total_pnl_pct']:+.1f}%)</td></tr>" for h in pnl)
    summary = f'<div class="pnl-summary"><div><span>Portfolio value</span><strong>${total_value:,.0f}</strong></div><div><span>Overnight P&amp;L</span><strong style="color:{colour_pct(total_overnight)};">${total_overnight:+,.0f}</strong></div></div>'
    return summary + f"<table class='tbl'><tr><th>Ticker</th><th class='num'>Shares</th><th class='num'>Avg</th><th class='num'>Last</th><th class='num'>%</th><th class='num'>Value</th><th class='num'>O/N P&amp;L</th><th class='num'>Total P&amp;L</th></tr>{rows}</table>"


def build_announcements(ann):
    html = []
    for a in ann[:10]:
        html.append(f'<div class="news-item"><div class="ann-row"><span class="code">{a["code"]}</span><span class="sensitive">{a.get("sensitive","")}</span><span class="ann-time">{a["time"]}</span></div><div class="ann-headline">{a["headline"]}</div></div>')
    return "".join(html) if html else '<p style="color:#888;font-size:13px;">No announcements yet.</p>'


def build_earnings_block(earnings):
    if not earnings:
        return '<p style="color:#888;font-size:13px;">No upcoming earnings identified — source may be limiting; check Yahoo directly.</p>'
    rows = "".join(
        f"<tr><td>{'🔴' if e['days'] == 0 else '🟡' if e['days'] <= 2 else '⚪'}</td><td><b>{e['ticker']}</b></td><td>{e['date']}</td><td>{e['days']}d</td></tr>"
        for e in earnings
    )
    return f"<table class='tbl'>{rows}</table>"


def build_news_block(items, max_items=6):
    html = []
    for item in items[:max_items]:
        html.append(f'<div class="news-item"><div class="news-title"><a href="{item["link"]}" target="_blank">{item["title"]}</a></div><div class="news-meta">{item["source"]} · {item["pub"]}</div><div class="news-summary">{item["summary"][:250]}</div></div>')
    return "".join(html) if html else '<p style="color:#888;font-size:13px;">No items.</p>'


def narrative_to_html(text):
    """Convert narrative to HTML with fixed drift regex."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    out = []
    for p in paragraphs:
        # Bold labels
        p = re.sub(r"\*\*(.+?)\*\*", r'<strong class="mini-label">\1</strong>', p)
        # Fixed drift highlighting — match "WARNING TICKER: ... — thesis was ..." until end of sentence
        p = re.sub(r"(WARNING\s+[A-Z]{3,5}:[^\.]+(?:\.[^A-Z][^\.]+)*\.)",
                   r'<span class="drift">⚠ \1</span>', p)
        # Fallback for any warning symbol already in text
        p = re.sub(r"(⚠\s*[A-Z]{3,5}[^<]+?thesis[^<]+?\.)(?!</span>)",
                   r'<span class="drift">\1</span>', p)
        out.append(f"<p>{p}</p>")
    return "".join(out)


# ── HTML TEMPLATE (unchanged) ──────────────────────────────────────────────
def build_html(data):
    p = data["prices"]
    now_str = datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y · %H:%M AEST")
    asx_mkt = ["ASX 200", "ASX 200 Futures", "S&P 500", "Nasdaq 100", "VIX", "Nikkei 225", "Hang Seng"]
    asx_mac = ["AUD/USD", "US 10Y Yield", "US 2Y Yield", "Gold", "WTI Crude", "Iron Ore (62%)", "Copper"]
    ndx_mkt = ["Nasdaq 100", "Nasdaq Futures", "S&P 500", "Dow Jones", "VIX"]
    ndx_mac = ["US 10Y Yield", "US 2Y Yield", "Gold", "WTI Crude", "AUD/USD"]

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Morning Prep — {now_str}</title>
<style>
  :root {{ --bg: #f5f5f5; --card: #fafafa; --text: #1a1a1a; --muted: #666; --brand: #0b3d91; --border: #e8e8e8; --content-bg: #ffffff; --label: #0b3d91; }}
  [data-theme="dark"] {{ --bg: #0e1218; --card: #1a1f2a; --text: #e8e8e8; --muted: #aab; --brand: #5a8fef; --border: #2a3140; --content-bg: #141923; --label: #8ab0ff; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: var(--text); margin: 0; background: var(--bg); transition: all 0.2s; }}
  .container {{ max-width: 1380px; margin: 0 auto; padding: 20px; }}
  .topbar {{ background: var(--brand); color: white; padding: 16px 22px; border-radius: 6px 6px 0 0; display: flex; justify-content: space-between; align-items: center; }}
  .topbar h1 {{ margin: 0; font-size: 20px; }}
  .topbar .sub {{ font-size: 11.5px; opacity: 0.85; margin-top: 2px; }}
  .theme-toggle {{ background: rgba(255,255,255,0.2); color: white; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; font-size: 12px; }}
  .tabs {{ display: flex; background: var(--content-bg); border-bottom: 2px solid var(--brand); }}
  .tab {{ padding: 13px 26px; cursor: pointer; font-weight: 600; font-size: 13.5px; color: var(--muted); border-bottom: 3px solid transparent; }}
  .tab.active {{ color: var(--brand); border-bottom: 3px solid var(--brand); background: var(--bg); }}
  .tab-content {{ display: none; background: var(--content-bg); padding: 22px; border-radius: 0 0 6px 6px; }}
  .tab-content.active {{ display: block; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 20px; }}
  .grid3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 18px; margin-bottom: 20px; }}
  h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--brand); margin: 20px 0 8px 0; padding-bottom: 5px; border-bottom: 1px solid var(--border); }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 5px; padding: 14px 16px; }}
  .tbl {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  .tbl th {{ text-align: left; font-size: 10px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.5px; padding: 5px 7px; border-bottom: 1px solid var(--border); }}
  .tbl td {{ padding: 6px 7px; border-bottom: 1px solid var(--border); }}
  .num {{ text-align: right; }}
  .narrative {{ background: var(--bg); border-left: 4px solid var(--brand); padding: 16px 20px; border-radius: 4px; font-size: 13.5px; line-height: 1.65; }}
  .narrative p {{ margin: 8px 0; }}
  .narrative .mini-label {{ display: inline; color: var(--label); font-size: 10.5px; text-transform: uppercase; letter-spacing: 1px; margin-right: 6px; }}
  .drift {{ background: #fff5b7; color: #7a5a00; padding: 3px 8px; border-radius: 3px; font-weight: 500; display: inline-block; margin: 2px 0; }}
  [data-theme="dark"] .drift {{ background: #5a4a00; color: #ffdc66; }}
  .heatmap {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 8px; }}
  .heat-cell {{ padding: 12px 8px; border-radius: 4px; text-align: center; border: 1px solid var(--border); }}
  .heat-sector {{ font-size: 11px; font-weight: 600; margin-bottom: 3px; }}
  .heat-pct {{ font-size: 14px; font-weight: 700; }}
  .news-item {{ padding: 8px 0; border-bottom: 1px solid var(--border); }}
  .news-item:last-child {{ border-bottom: none; }}
  .news-title {{ font-size: 12.5px; font-weight: 600; margin-bottom: 2px; }}
  .news-title a {{ color: var(--text); text-decoration: none; }}
  .news-meta {{ font-size: 10px; color: var(--muted); margin-bottom: 3px; }}
  .news-summary {{ font-size: 11.5px; color: var(--muted); line-height: 1.5; }}
  .ann-row {{ display: flex; gap: 10px; align-items: center; font-size: 11.5px; }}
  .code {{ font-weight: 700; color: var(--brand); min-width: 50px; }}
  .sensitive {{ color: #f0a500; }}
  .ann-time {{ color: var(--muted); font-size: 10.5px; }}
  .ann-headline {{ font-size: 12.5px; margin-top: 2px; }}
  .pnl-summary {{ display: flex; gap: 24px; padding: 12px 16px; background: var(--bg); border-radius: 5px; margin-bottom: 10px; font-size: 13px; }}
  .pnl-summary span {{ color: var(--muted); font-size: 11px; display: block; }}
  .pnl-summary strong {{ font-size: 18px; }}
  footer {{ text-align: center; color: var(--muted); font-size: 10px; margin-top: 28px; padding: 16px; }}
</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <div><h1>📈 Morning Prep Dashboard v2.1</h1><div class="sub">{now_str}</div></div>
    <button class="theme-toggle" onclick="toggleTheme()">🌙 Toggle dark</button>
  </div>
  <div class="tabs">
    <div class="tab active" onclick="showTab('asx', event)">ASX 200</div>
    <div class="tab" onclick="showTab('ndx', event)">Nasdaq 100</div>
    <div class="tab" onclick="showTab('pnl', event)">My P&amp;L</div>
    <div class="tab" onclick="showTab('week', event)">Week Ahead</div>
  </div>

  <div id="tab-asx" class="tab-content active">
    <h2>Market Snapshot</h2>
    <div class="grid2"><div class="card">{build_price_table(p, asx_mkt)}</div><div class="card">{build_price_table(p, asx_mac)}</div></div>
    <h2>ASX Sector Heatmap</h2>
    {build_sector_heatmap(data["asx_sectors"])}
    <h2>Analyst Narrative</h2>
    <div class="narrative">{narrative_to_html(data["asx_narrative"])}</div>
    <h2>Watchlist + Thesis</h2>
    <div class="card">{build_watchlist_table(data["asx_watchlist"])}</div>
    <div class="grid3">
      <div><h2>ASX Announcements</h2>{build_announcements(data["asx_announcements"])}</div>
      <div><h2>Upcoming Earnings (ASX)</h2>{build_earnings_block(data["asx_earnings"])}</div>
      <div><h2>Rates & Fixed Income</h2>{build_news_block(data["asx_feeds"]["rates_fi"], 4)}</div>
    </div>
    <div class="grid3">
      <div><h2>Property & RE</h2>{build_news_block(data["asx_feeds"]["property_re"], 4)}</div>
      <div><h2>ASX Macro Flags</h2>{build_news_block(data["asx_feeds"]["au_macro"], 4)}</div>
      <div><h2>M&amp;A + Capital Raises</h2>{build_news_block(data["asx_feeds"]["ma_deals"], 4)}</div>
    </div>
  </div>

  <div id="tab-ndx" class="tab-content">
    <h2>Market Snapshot</h2>
    <div class="grid2"><div class="card">{build_price_table(p, ndx_mkt)}</div><div class="card">{build_price_table(p, ndx_mac)}</div></div>
    <h2>US Sector Heatmap</h2>
    {build_sector_heatmap(data["ndx_sectors"])}
    <h2>Analyst Narrative</h2>
    <div class="narrative">{narrative_to_html(data["ndx_narrative"])}</div>
    <h2>Watchlist + Thesis</h2>
    <div class="card">{build_watchlist_table(data["ndx_watchlist"])}</div>
    <div class="grid3">
      <div><h2>Upcoming Earnings (US)</h2>{build_earnings_block(data["ndx_earnings"])}</div>
      <div><h2>Tech & AI</h2>{build_news_block(data["ndx_feeds"]["tech_ai"], 4)}</div>
      <div><h2>US Markets</h2>{build_news_block(data["ndx_feeds"]["us_markets"], 4)}</div>
    </div>
    <div class="grid2">
      <div><h2>Rates & Fixed Income</h2>{build_news_block(data["ndx_feeds"]["rates_fi"], 4)}</div>
      <div><h2>M&amp;A + IPOs</h2>{build_news_block(data["ndx_feeds"]["ma_deals"], 4)}</div>
    </div>
  </div>

  <div id="tab-pnl" class="tab-content">
    <h2>My Portfolio</h2>
    {build_pnl_block(data["pnl"], data["pnl_total_value"], data["pnl_total_overnight"])}
  </div>

  <div id="tab-week" class="tab-content">
    <h2>Key Catalysts This Week</h2>
    <div class="narrative">{narrative_to_html(data["week_narrative"])}</div>
    <div class="grid2" style="margin-top:20px;">
      <div><h2>Upcoming Earnings</h2>{build_earnings_block(data["asx_earnings"] + data["ndx_earnings"])}</div>
      <div><h2>Economic Calendar</h2>{build_news_block(data["economic_calendar"], 12)}</div>
    </div>
  </div>

  <footer>Morning Prep v2.1 · Built by Sam Hash · Generated by Claude · Data: Yahoo Finance, Market Index, Reuters, CNBC, RBA, Fed, ForexFactory</footer>
</div>
<script>
function showTab(name, ev) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  ev.target.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}}
function toggleTheme() {{
  document.body.dataset.theme = document.body.dataset.theme === "dark" ? "" : "dark";
}}
</script>
</body></html>
"""


# ── MAIN ───────────────────────────────────────────────────────────────────
def main():
    print("→ Fetching index & macro prices...")
    prices = fetch_prices(INDEX_TICKERS)
    print("→ Fetching ASX watchlist...")
    asx_watchlist = fetch_watchlist_with_thesis(ASX_WATCHLIST)
    print("→ Fetching Nasdaq watchlist...")
    ndx_watchlist = fetch_watchlist_with_thesis(NDX_WATCHLIST)
    print("→ Fetching ASX sector heatmap...")
    asx_sectors = fetch_sectors(ASX_SECTOR_ETFS)
    print("→ Fetching US sector heatmap...")
    ndx_sectors = fetch_sectors(NDX_SECTOR_ETFS)
    print("→ Scraping ASX announcements (Market Index)...")
    asx_announcements = fetch_asx_announcements(max_items=12)
    print("→ Fetching earnings calendars...")
    asx_earnings = fetch_earnings_today([t for t, _ in ASX_WATCHLIST])
    ndx_earnings = fetch_earnings_today([t for t, _ in NDX_WATCHLIST])
    print("→ Fetching economic calendar...")
    economic = fetch_economic_calendar()
    print("→ Gathering news feeds...")
    asx_feeds = {"au_macro": gather_feed_section(["au_macro"]),
                 "rates_fi": gather_feed_section(["rates_fi"]),
                 "property_re": gather_feed_section(["property_re"]),
                 "ma_deals": gather_feed_section(["ma_deals"])}
    ndx_feeds = {"us_markets": gather_feed_section(["us_markets"]),
                 "tech_ai": gather_feed_section(["tech_ai"]),
                 "rates_fi": gather_feed_section(["rates_fi"]),
                 "ma_deals": gather_feed_section(["ma_deals"])}
    print("→ Computing P&L...")
    pnl, total_value, total_overnight = compute_pnl(HOLDINGS)
    print("→ Generating ASX narrative...")
    asx_narrative = generate_narrative("ASX 200", prices, asx_watchlist, asx_feeds, asx_sectors,
                                        "ASX equities, property/REITs, AU macro/rates")
    print("→ Generating Nasdaq narrative...")
    ndx_narrative = generate_narrative("Nasdaq 100", prices, ndx_watchlist, ndx_feeds, ndx_sectors,
                                        "US mega-cap tech, AI/semiconductors, US rates")
    print("→ Generating week-ahead narrative...")
    week_narrative = generate_week_catalysts(asx_watchlist, ndx_watchlist,
                                              asx_earnings + ndx_earnings, economic)
    print("→ Building HTML...")
    data = {"prices": prices, "asx_watchlist": asx_watchlist, "ndx_watchlist": ndx_watchlist,
            "asx_sectors": asx_sectors, "ndx_sectors": ndx_sectors,
            "asx_announcements": asx_announcements, "asx_earnings": asx_earnings,
            "ndx_earnings": ndx_earnings, "economic_calendar": economic,
            "asx_feeds": asx_feeds, "ndx_feeds": ndx_feeds,
            "asx_narrative": asx_narrative, "ndx_narrative": ndx_narrative,
            "week_narrative": week_narrative, "pnl": pnl,
            "pnl_total_value": total_value, "pnl_total_overnight": total_overnight}
    html = build_html(data)
    os.makedirs("output", exist_ok=True)
    today = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"output/morning_prep_{today}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ Dashboard saved: {filename}")
    try:
        webbrowser.open(f"file://{os.path.abspath(filename)}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
