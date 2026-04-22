#!/usr/bin/env python3
"""
Morning Prep Dashboard v2 — ASX 200 + Nasdaq 100
─────────────────────────────────────────────────
Refinements from v1:
  1. Robust ASX announcements scraping (direct from asx.com.au)
  2. Graceful handling of stale/missing ticker data
  3. Sector heatmap (ASX + Nasdaq sector breakdown)
  4. Earnings calendar for both markets
  5. Economic calendar (data releases today/this week)
  6. P&L tracker (configure your holdings for overnight P&L)
  7. Thesis-drift alerts (store 1-line thesis per watchlist stock)
  8. Key catalysts this week section
  9. Dark mode toggle
  10. Tighter error handling throughout

Run locally:
    python3 morning_prep.py

Author: Sam Hash, 2026
"""

import os
import re
import sys
import json
import feedparser
import yfinance as yf
import anthropic
import pytz
from datetime import datetime, timedelta
from dataclasses import dataclass
from bs4 import BeautifulSoup
import requests
import webbrowser

# ── CONFIG ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36"}

# Indices, FX, commodities
INDEX_TICKERS = {
    "ASX 200":         "^AXJO",
    "ASX 200 Futures": "AP=F",
    "S&P 500":         "^GSPC",
    "Nasdaq 100":      "^NDX",
    "Nasdaq Futures":  "NQ=F",
    "Dow Jones":       "^DJI",
    "VIX":             "^VIX",
    "Nikkei 225":      "^N225",
    "Hang Seng":       "^HSI",
    "AUD/USD":         "AUDUSD=X",
    "Gold":            "GC=F",
    "WTI Crude":       "CL=F",
    "Iron Ore (62%)":  "TIO=F",
    "Copper":          "HG=F",
    "US 10Y Yield":    "^TNX",
    "US 2Y Yield":     "^IRX",
}

# ── WATCHLIST WITH THESIS ─────────────────────────────────────────────────
# Each entry: (ticker, 1-line thesis) — the thesis is used for drift detection
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

# ── YOUR HOLDINGS (for P&L tracker) ───────────────────────────────────────
# Format: (ticker, shares_held, avg_cost)
# Update these to match your actual portfolio
HOLDINGS = [
    ("NVDA", 10,  120.00),
    ("AMD",  25,   95.00),
    ("TSM",  15,  150.00),
]

# ── SECTOR ETFS (for heatmap) ─────────────────────────────────────────────
ASX_SECTOR_ETFS = {
    "Financials":     "OZF.AX",
    "Materials":      "OZR.AX",
    "Resources":      "QRE.AX",
    "Energy":         "FUEL.AX",
    "Healthcare":     "IXJ.AX",
    "Technology":     "NDQ.AX",
    "Industrials":    "SUBD.AX",
    "REITs":          "VAP.AX",
    "Consumer Disc":  "IXI.AX",
    "Consumer Stap":  "IXR.AX",
}

NDX_SECTOR_ETFS = {
    "Technology":     "XLK",
    "Comm Services":  "XLC",
    "Consumer Disc":  "XLY",
    "Healthcare":     "XLV",
    "Financials":     "XLF",
    "Industrials":    "XLI",
    "Energy":         "XLE",
    "Materials":      "XLB",
    "Utilities":      "XLU",
    "Real Estate":    "XLRE",
    "Semiconductors": "SOXX",
}

# ── NEWS FEEDS ────────────────────────────────────────────────────────────
FEEDS = {
    "au_macro": [
        ("RBA Media",        "https://www.rba.gov.au/rss/rss-cb-media-releases.xml"),
        ("Reuters AU",       "https://feeds.reuters.com/reuters/AUdomesticNews"),
    ],
    "rates_fi": [
        ("Reuters Bonds",    "https://feeds.reuters.com/reuters/bondsNews"),
        ("MarketWatch Bonds","https://feeds.marketwatch.com/marketwatch/bondmarket/"),
        ("Fed Press",        "https://www.federalreserve.gov/feeds/press_all.xml"),
    ],
    "property_re": [
        ("Urban Developer",  "https://theurbandeveloper.com/feed"),
        ("AU Prop Journal",  "https://www.apijournal.com.au/feed/"),
    ],
    "ma_deals": [
        ("Reuters M&A",      "https://feeds.reuters.com/reuters/mergersNews"),
        ("Reuters IPO",      "https://feeds.reuters.com/reuters/IPOsnews"),
    ],
    "tech_ai": [
        ("CNBC Tech",        "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
        ("Reuters Tech",     "https://feeds.reuters.com/reuters/technologyNews"),
    ],
    "us_markets": [
        ("Reuters Markets",  "https://feeds.reuters.com/reuters/marketsNews"),
        ("CNBC Markets",     "https://www.cnbc.com/id/20409666/device/rss/rss.html"),
    ],
}

# ── PRICE FETCHING (with freshness check) ─────────────────────────────────
def fetch_price_row(ticker: str) -> dict:
    """Fetch a single ticker with freshness & error handling."""
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

        return {
            "last": round(last, 2),
            "pct":  round(pct, 2),
            "status": "ok" if days_old <= 1 else "stale",
            "days_old": days_old,
        }
    except Exception as e:
        return {"last": None, "pct": None, "status": "error", "err": str(e)[:60]}


def fetch_prices(tickers: dict) -> dict:
    return {name: fetch_price_row(ticker) for name, ticker in tickers.items()}


def fetch_watchlist_with_thesis(items: list[tuple]) -> list[dict]:
    rows = []
    for ticker, thesis in items:
        p = fetch_price_row(ticker)
        rows.append({
            "ticker": ticker.replace(".AX", ""),
            "raw_ticker": ticker,
            "thesis": thesis,
            **p,
        })
    return rows


# ── ASX ANNOUNCEMENTS (robust scraping) ──────────────────────────────────
def fetch_asx_announcements(max_items: int = 12) -> list[dict]:
    """Scrape today's ASX market-sensitive announcements directly from ASX."""
    url = "https://www.asx.com.au/asx/statistics/todayAnns.do"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = []
        table = soup.find("table") or soup.find("tbody")
        if table:
            for tr in table.find_all("tr")[:max_items]:
                cells = tr.find_all("td")
                if len(cells) >= 4:
                    code = cells[0].get_text(strip=True)
                    time_str = cells[1].get_text(strip=True)
                    headline = cells[2].get_text(strip=True)
                    sensitive = "★" if "price" in " ".join(c.get_text() for c in cells).lower() else ""
                    rows.append({
                        "code": code,
                        "time": time_str,
                        "headline": headline,
                        "sensitive": sensitive,
                    })
        # If scraping fails, fall back to RSS
        if not rows:
            feed = feedparser.parse("https://www.asx.com.au/asx/1/company/announcements.rss")
            for entry in feed.entries[:max_items]:
                rows.append({
                    "code": "—",
                    "time": entry.get("published", "")[:16],
                    "headline": entry.get("title", ""),
                    "sensitive": "",
                })
        return rows
    except Exception as e:
        return [{"code": "ERR", "time": "", "headline": f"ASX fetch failed: {e}", "sensitive": ""}]


# ── SECTOR HEATMAP ────────────────────────────────────────────────────────
def fetch_sectors(sector_map: dict) -> list[dict]:
    out = []
    for name, ticker in sector_map.items():
        p = fetch_price_row(ticker)
        out.append({"sector": name, "ticker": ticker, **p})
    return out


# ── EARNINGS & ECONOMIC CALENDAR ──────────────────────────────────────────
def fetch_earnings_today(tickers: list[str]) -> list[dict]:
    """Return companies from the watchlist with earnings dates in the next 7 days."""
    out = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is not None and hasattr(cal, "empty") and not cal.empty:
                # yfinance calendar format varies — try a few shapes
                earnings_date = None
                if "Earnings Date" in cal.index:
                    earnings_date = cal.loc["Earnings Date"][0]
                elif isinstance(cal, dict) and "Earnings Date" in cal:
                    earnings_date = cal["Earnings Date"][0] if cal["Earnings Date"] else None
                if earnings_date:
                    date_str = str(earnings_date)[:10]
                    try:
                        d = datetime.strptime(date_str, "%Y-%m-%d").date()
                        days_away = (d - datetime.now().date()).days
                        if 0 <= days_away <= 7:
                            out.append({"ticker": ticker.replace(".AX", ""), "date": date_str, "days": days_away})
                    except Exception:
                        pass
        except Exception:
            pass
    return sorted(out, key=lambda x: x["days"])


def fetch_economic_calendar() -> list[dict]:
    """Pull key economic releases via Investing.com or ForexFactory RSS."""
    # Using a simple RSS feed for economic calendar
    events = []
    try:
        feed = feedparser.parse("https://nfs.faireconomy.media/ff_calendar_thisweek.xml")
        for entry in feed.entries[:20]:
            events.append({
                "title": entry.get("title", ""),
                "date":  entry.get("published", "")[:16],
                "link":  entry.get("link", ""),
            })
    except Exception:
        pass
    return events


# ── RSS NEWS ──────────────────────────────────────────────────────────────
def fetch_feed(name: str, url: str, max_items: int = 5) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        out = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            summary = re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()[:350]
            out.append({
                "source": name,
                "title":  title,
                "summary": summary,
                "link":   entry.get("link", ""),
                "pub":    entry.get("published", "")[:16],
            })
        return out
    except Exception as e:
        return [{"source": name, "title": f"Feed error: {e}", "summary": "", "link": "", "pub": ""}]


def gather_feed_section(keys: list[str], max_per_feed: int = 4) -> list[dict]:
    out = []
    for k in keys:
        for name, url in FEEDS.get(k, []):
            out.extend(fetch_feed(name, url, max_per_feed))
    return out


# ── CLAUDE ANALYSIS ───────────────────────────────────────────────────────
def ask_claude(prompt: str, max_tokens: int = 1800) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def generate_narrative(market_label: str, prices: dict, watchlist: list[dict],
                       feeds: dict, sectors: list[dict], focus: str) -> str:
    watchlist_text = "\n".join(
        f"{r['ticker']}: {r.get('last', '—')} ({r.get('pct', 0):+.2f}%) — Thesis: {r['thesis']}"
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

Write ~450 words covering {market_label} context for today. Structure with bold mini-section labels (no headers, punchy paragraphs):

**OVERNIGHT SETUP** — close details, futures, likely open tone.
**RATES & MACRO** — yield curve moves, central bank commentary, FX implications.
**SECTOR ROTATION** — use the sector heatmap data to flag leaders and laggards.
**THESIS-DRIFT WATCH** — for any watchlist stock where overnight news or price action contradicts the stated thesis, flag it explicitly. Use the format: "⚠ [TICKER]: [what's changed] vs thesis of [thesis]."
**CATALYSTS TODAY** — company-specific news, data releases, central bank speakers.
**NAMES TO WATCH** — 2-3 specific tickers with concrete catalysts today.

Tight, analyst-grade prose. No fluff. Be specific, not generic.

--- PRICE DATA ---
{prices_text}

--- SECTOR HEATMAP ---
{sector_text}

--- WATCHLIST (ticker: price %, thesis) ---
{watchlist_text}

--- NEWS FEEDS ---
{feed_text}
"""
    return ask_claude(prompt, max_tokens=2000)


def generate_week_catalysts(asx_watchlist: list[dict], ndx_watchlist: list[dict],
                             earnings: list[dict], economic: list[dict]) -> str:
    earnings_text = "\n".join(f"{e['ticker']}: {e['date']} ({e['days']}d away)" for e in earnings[:15])
    econ_text = "\n".join(f"• {e['title']} ({e['date']})" for e in economic[:15])
    all_stocks = "\n".join(f"{r['ticker']}: {r['thesis']}" for r in asx_watchlist + ndx_watchlist)

    prompt = f"""Produce a "Key Catalysts This Week" forward-looking section, ~200 words.

Structure:
**EARNINGS** — prioritise watchlist names reporting this week. Flag what to watch for in each.
**DATA & CENTRAL BANKS** — key economic releases and central bank speakers.
**MACRO THEMES** — 2-3 themes playing out this week worth watching.

Concise, forward-looking, no speculation beyond what's scheduled.

--- UPCOMING EARNINGS ---
{earnings_text}

--- ECONOMIC CALENDAR ---
{econ_text}

--- WATCHLIST CONTEXT ---
{all_stocks}
"""
    return ask_claude(prompt, max_tokens=800)


# ── P&L CALCULATION ───────────────────────────────────────────────────────
def compute_pnl(holdings: list[tuple]) -> list[dict]:
    out = []
    total_value = 0
    total_overnight = 0
    for ticker, shares, avg_cost in holdings:
        p = fetch_price_row(ticker)
        if p["status"] == "ok" and p["last"]:
            position_value = p["last"] * shares
            overnight_pnl  = position_value * p["pct"] / 100
            total_pnl      = (p["last"] - avg_cost) * shares
            total_pnl_pct  = ((p["last"] - avg_cost) / avg_cost) * 100 if avg_cost else 0
            out.append({
                "ticker": ticker,
                "shares": shares,
                "avg_cost": avg_cost,
                "last":   p["last"],
                "pct":    p["pct"],
                "value":  round(position_value, 2),
                "overnight_pnl": round(overnight_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl_pct, 2),
            })
            total_value += position_value
            total_overnight += overnight_pnl
    return out, round(total_value, 2), round(total_overnight, 2)


# ── HTML BUILDERS ─────────────────────────────────────────────────────────
def colour_pct(pct):
    if pct is None:
        return "#888"
    return "#0a7a0a" if pct > 0 else ("#c62828" if pct < 0 else "#666")


def arrow(pct):
    if pct is None:
        return "—"
    return "▲" if pct > 0 else ("▼" if pct < 0 else "—")


def price_cell(v: dict) -> str:
    status = v.get("status", "error")
    if status in ("error", "no_data"):
        return '<span style="color:#aaa;font-size:11px;">unavail</span>'
    if status == "stale":
        return f'<span style="color:#999;font-size:11px;">stale ({v.get("days_old",0)}d)</span>'
    pct = v.get("pct")
    return f'<span style="color:{colour_pct(pct)};font-weight:600;">{arrow(pct)} {pct:+.2f}%</span>'


def build_price_table(prices: dict, keys: list[str]) -> str:
    rows = []
    for k in keys:
        if k in prices:
            v = prices[k]
            last = v.get("last") if v.get("last") is not None else "—"
            rows.append(f"<tr><td>{k}</td><td class='num'>{last}</td><td class='num'>{price_cell(v)}</td></tr>")
    return f"<table class='tbl'><tr><th>Market</th><th class='num'>Level</th><th class='num'>Chg</th></tr>{''.join(rows)}</table>"


def build_sector_heatmap(sectors: list[dict]) -> str:
    cells = []
    for s in sectors:
        pct = s.get("pct") or 0
        if s.get("status") not in ("ok", "stale"):
            bg = "#eaeaea"
            colour = "#aaa"
            label = "—"
        else:
            # Gradient: red to green via white at 0
            if pct > 0:
                intensity = min(abs(pct) / 3, 1)
                bg = f"rgba(10, 122, 10, {0.15 + intensity * 0.45})"
            elif pct < 0:
                intensity = min(abs(pct) / 3, 1)
                bg = f"rgba(198, 40, 40, {0.15 + intensity * 0.45})"
            else:
                bg = "#f7f7f7"
            colour = "#111"
            label = f"{pct:+.2f}%"
        cells.append(f"""
          <div class="heat-cell" style="background:{bg};color:{colour};">
            <div class="heat-sector">{s['sector']}</div>
            <div class="heat-pct">{label}</div>
          </div>
        """)
    return f'<div class="heatmap">{"".join(cells)}</div>'


def build_watchlist_table(rows: list[dict]) -> str:
    tr = []
    for r in rows:
        pct = r.get("pct")
        last = r.get("last") if r.get("last") is not None else "—"
        pct_str = f"{pct:+.2f}%" if pct is not None else "—"
        tr.append(f"""
          <tr>
            <td><b>{r['ticker']}</b></td>
            <td class='num'>{last}</td>
            <td class='num' style='color:{colour_pct(pct)};font-weight:600;'>{pct_str}</td>
            <td style='font-size:11.5px;color:#666;'>{r['thesis']}</td>
          </tr>
        """)
    return f"""
    <table class='tbl'>
      <tr><th>Ticker</th><th class='num'>Last</th><th class='num'>%</th><th>Thesis</th></tr>
      {''.join(tr)}
    </table>
    """


def build_pnl_block(holdings_pnl: list[dict], total_value: float, total_overnight: float) -> str:
    if not holdings_pnl:
        return '<div class="card" style="color:#888;font-size:13px;">No holdings configured. Edit HOLDINGS in the script.</div>'
    rows = []
    for h in holdings_pnl:
        rows.append(f"""
          <tr>
            <td><b>{h['ticker']}</b></td>
            <td class='num'>{h['shares']}</td>
            <td class='num'>${h['avg_cost']:.2f}</td>
            <td class='num'>${h['last']:.2f}</td>
            <td class='num' style='color:{colour_pct(h["pct"])};'>{h["pct"]:+.2f}%</td>
            <td class='num'>${h['value']:,.0f}</td>
            <td class='num' style='color:{colour_pct(h["overnight_pnl"])};font-weight:600;'>${h['overnight_pnl']:+,.0f}</td>
            <td class='num' style='color:{colour_pct(h["total_pnl"])};font-weight:600;'>${h['total_pnl']:+,.0f} ({h['total_pnl_pct']:+.1f}%)</td>
          </tr>
        """)
    summary = f"""
      <div class="pnl-summary">
        <div><span>Portfolio value</span><strong>${total_value:,.0f}</strong></div>
        <div><span>Overnight P&amp;L</span><strong style='color:{colour_pct(total_overnight)};'>${total_overnight:+,.0f}</strong></div>
      </div>
    """
    return summary + f"""
      <table class='tbl'>
        <tr><th>Ticker</th><th class='num'>Shares</th><th class='num'>Avg Cost</th><th class='num'>Last</th><th class='num'>%</th><th class='num'>Value</th><th class='num'>O/N P&amp;L</th><th class='num'>Total P&amp;L</th></tr>
        {''.join(rows)}
      </table>
    """


def build_announcements(ann: list[dict]) -> str:
    html = []
    for a in ann[:10]:
        html.append(f"""
          <div class="news-item">
            <div class="ann-row">
              <span class="code">{a['code']}</span>
              <span class="sensitive">{a.get('sensitive','')}</span>
              <span class="ann-time">{a['time']}</span>
            </div>
            <div class="ann-headline">{a['headline']}</div>
          </div>
        """)
    return "".join(html) if html else '<p style="color:#888;font-size:13px;">No announcements yet.</p>'


def build_earnings_block(earnings: list[dict]) -> str:
    if not earnings:
        return '<p style="color:#888;font-size:13px;">No upcoming earnings in the next 7 days.</p>'
    rows = []
    for e in earnings:
        badge = "🔴" if e["days"] == 0 else ("🟡" if e["days"] <= 2 else "⚪")
        rows.append(f"<tr><td>{badge}</td><td><b>{e['ticker']}</b></td><td>{e['date']}</td><td>{e['days']}d</td></tr>")
    return f"<table class='tbl'>{''.join(rows)}</table>"


def build_news_block(items: list[dict], max_items: int = 6) -> str:
    html = []
    for item in items[:max_items]:
        html.append(f"""
          <div class="news-item">
            <div class="news-title"><a href="{item['link']}" target="_blank">{item['title']}</a></div>
            <div class="news-meta">{item['source']} · {item['pub']}</div>
            <div class="news-summary">{item['summary'][:250]}</div>
          </div>
        """)
    return "".join(html) if html else '<p style="color:#888;font-size:13px;">No items.</p>'


def narrative_to_html(text: str) -> str:
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    out = []
    for p in paragraphs:
        p = re.sub(r"\*\*(.+?)\*\*", r'<strong class="mini-label">\1</strong>', p)
        # Highlight drift warnings
        p = re.sub(r"(⚠[^\.]+\.)", r'<span class="drift">\1</span>', p)
        out.append(f"<p>{p}</p>")
    return "".join(out)


# ── FULL HTML TEMPLATE ────────────────────────────────────────────────────
def build_html(data: dict) -> str:
    p = data["prices"]
    now_str = datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y · %H:%M AEST")

    asx_mkt = ["ASX 200", "ASX 200 Futures", "S&P 500", "Nasdaq 100", "VIX", "Nikkei 225", "Hang Seng"]
    asx_mac = ["AUD/USD", "US 10Y Yield", "US 2Y Yield", "Gold", "WTI Crude", "Iron Ore (62%)", "Copper"]
    ndx_mkt = ["Nasdaq 100", "Nasdaq Futures", "S&P 500", "Dow Jones", "VIX"]
    ndx_mac = ["US 10Y Yield", "US 2Y Yield", "Gold", "WTI Crude", "AUD/USD"]

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Morning Prep — {now_str}</title>
<style>
  :root {{
    --bg: #f5f5f5; --card: #fafafa; --text: #1a1a1a; --muted: #666; --brand: #0b3d91;
    --border: #e8e8e8; --content-bg: #ffffff; --label: #0b3d91;
  }}
  [data-theme="dark"] {{
    --bg: #0e1218; --card: #1a1f2a; --text: #e8e8e8; --muted: #aab;
    --brand: #5a8fef; --border: #2a3140; --content-bg: #141923; --label: #8ab0ff;
  }}
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
  .drift {{ background: #fff5b7; color: #7a5a00; padding: 2px 6px; border-radius: 3px; font-weight: 600; }}
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
    <div>
      <h1>📈 Morning Prep Dashboard v2</h1>
      <div class="sub">{now_str}</div>
    </div>
    <button class="theme-toggle" onclick="toggleTheme()">🌙 Toggle dark</button>
  </div>

  <div class="tabs">
    <div class="tab active" onclick="showTab('asx', event)">ASX 200</div>
    <div class="tab" onclick="showTab('ndx', event)">Nasdaq 100</div>
    <div class="tab" onclick="showTab('pnl', event)">My P&amp;L</div>
    <div class="tab" onclick="showTab('week', event)">Week Ahead</div>
  </div>

  <!-- ━━━━━━━━━━━━━━━━━━ ASX TAB ━━━━━━━━━━━━━━━━━━ -->
  <div id="tab-asx" class="tab-content active">

    <h2>Market Snapshot</h2>
    <div class="grid2">
      <div class="card">{build_price_table(p, asx_mkt)}</div>
      <div class="card">{build_price_table(p, asx_mac)}</div>
    </div>

    <h2>ASX Sector Heatmap</h2>
    {build_sector_heatmap(data["asx_sectors"])}

    <h2>Analyst Narrative</h2>
    <div class="narrative">{narrative_to_html(data["asx_narrative"])}</div>

    <h2>Watchlist + Thesis</h2>
    <div class="card">{build_watchlist_table(data["asx_watchlist"])}</div>

    <div class="grid3">
      <div>
        <h2>ASX Announcements</h2>
        {build_announcements(data["asx_announcements"])}
      </div>
      <div>
        <h2>Upcoming Earnings (ASX)</h2>
        {build_earnings_block(data["asx_earnings"])}
      </div>
      <div>
        <h2>Rates & Fixed Income</h2>
        {build_news_block(data["asx_feeds"]["rates_fi"], 4)}
      </div>
    </div>

    <div class="grid3">
      <div>
        <h2>Property & RE</h2>
        {build_news_block(data["asx_feeds"]["property_re"], 4)}
      </div>
      <div>
        <h2>ASX Macro Flags</h2>
        {build_news_block(data["asx_feeds"]["au_macro"], 4)}
      </div>
      <div>
        <h2>M&amp;A + Capital Raises</h2>
        {build_news_block(data["asx_feeds"]["ma_deals"], 4)}
      </div>
    </div>

  </div>

  <!-- ━━━━━━━━━━━━━━━━━━ NDX TAB ━━━━━━━━━━━━━━━━━━ -->
  <div id="tab-ndx" class="tab-content">

    <h2>Market Snapshot</h2>
    <div class="grid2">
      <div class="card">{build_price_table(p, ndx_mkt)}</div>
      <div class="card">{build_price_table(p, ndx_mac)}</div>
    </div>

    <h2>US Sector Heatmap</h2>
    {build_sector_heatmap(data["ndx_sectors"])}

    <h2>Analyst Narrative</h2>
    <div class="narrative">{narrative_to_html(data["ndx_narrative"])}</div>

    <h2>Watchlist + Thesis</h2>
    <div class="card">{build_watchlist_table(data["ndx_watchlist"])}</div>

    <div class="grid3">
      <div>
        <h2>Upcoming Earnings (US)</h2>
        {build_earnings_block(data["ndx_earnings"])}
      </div>
      <div>
        <h2>Tech & AI</h2>
        {build_news_block(data["ndx_feeds"]["tech_ai"], 4)}
      </div>
      <div>
        <h2>US Markets</h2>
        {build_news_block(data["ndx_feeds"]["us_markets"], 4)}
      </div>
    </div>

    <div class="grid2">
      <div>
        <h2>Rates & Fixed Income</h2>
        {build_news_block(data["ndx_feeds"]["rates_fi"], 4)}
      </div>
      <div>
        <h2>M&amp;A + IPOs</h2>
        {build_news_block(data["ndx_feeds"]["ma_deals"], 4)}
      </div>
    </div>

  </div>

  <!-- ━━━━━━━━━━━━━━━━━━ P&L TAB ━━━━━━━━━━━━━━━━━━ -->
  <div id="tab-pnl" class="tab-content">
    <h2>My Portfolio</h2>
    {build_pnl_block(data["pnl"], data["pnl_total_value"], data["pnl_total_overnight"])}
  </div>

  <!-- ━━━━━━━━━━━━━━━━━━ WEEK AHEAD TAB ━━━━━━━━━━━━━━━━━━ -->
  <div id="tab-week" class="tab-content">
    <h2>Key Catalysts This Week</h2>
    <div class="narrative">{narrative_to_html(data["week_narrative"])}</div>

    <div class="grid2" style="margin-top:20px;">
      <div>
        <h2>Upcoming Earnings</h2>
        {build_earnings_block(data["asx_earnings"] + data["ndx_earnings"])}
      </div>
      <div>
        <h2>Economic Calendar</h2>
        {build_news_block(data["economic_calendar"], 10)}
      </div>
    </div>
  </div>

  <footer>Morning Prep v2 · Built by Sam Hash · Generated by Claude · Data: Yahoo Finance, ASX, Reuters, CNBC, RBA, Fed</footer>

</div>

<script>
function showTab(name, ev) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  ev.target.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}}
function toggleTheme() {{
  const isDark = document.body.dataset.theme === "dark";
  document.body.dataset.theme = isDark ? "" : "dark";
}}
</script>
</body>
</html>
"""


# ── MAIN ──────────────────────────────────────────────────────────────────
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

    print("→ Scraping ASX announcements...")
    asx_announcements = fetch_asx_announcements(max_items=12)

    print("→ Fetching earnings calendars...")
    asx_earnings = fetch_earnings_today([t for t, _ in ASX_WATCHLIST])
    ndx_earnings = fetch_earnings_today([t for t, _ in NDX_WATCHLIST])

    print("→ Fetching economic calendar...")
    economic = fetch_economic_calendar()

    print("→ Gathering news feeds...")
    asx_feeds = {
        "au_macro":    gather_feed_section(["au_macro"]),
        "rates_fi":    gather_feed_section(["rates_fi"]),
        "property_re": gather_feed_section(["property_re"]),
        "ma_deals":    gather_feed_section(["ma_deals"]),
    }
    ndx_feeds = {
        "us_markets": gather_feed_section(["us_markets"]),
        "tech_ai":    gather_feed_section(["tech_ai"]),
        "rates_fi":   gather_feed_section(["rates_fi"]),
        "ma_deals":   gather_feed_section(["ma_deals"]),
    }

    print("→ Computing P&L...")
    pnl, total_value, total_overnight = compute_pnl(HOLDINGS)

    print("→ Generating ASX narrative with Claude...")
    asx_narrative = generate_narrative("ASX 200", prices, asx_watchlist, asx_feeds, asx_sectors,
                                        "ASX equities, property/REITs, AU macro/rates")

    print("→ Generating Nasdaq narrative with Claude...")
    ndx_narrative = generate_narrative("Nasdaq 100", prices, ndx_watchlist, ndx_feeds, ndx_sectors,
                                        "US mega-cap tech, AI/semiconductors, US rates")

    print("→ Generating week-ahead narrative...")
    week_narrative = generate_week_catalysts(asx_watchlist, ndx_watchlist,
                                              asx_earnings + ndx_earnings, economic)

    print("→ Building HTML...")
    data = {
        "prices": prices,
        "asx_watchlist": asx_watchlist,
        "ndx_watchlist": ndx_watchlist,
        "asx_sectors": asx_sectors,
        "ndx_sectors": ndx_sectors,
        "asx_announcements": asx_announcements,
        "asx_earnings": asx_earnings,
        "ndx_earnings": ndx_earnings,
        "economic_calendar": economic,
        "asx_feeds": asx_feeds,
        "ndx_feeds": ndx_feeds,
        "asx_narrative": asx_narrative,
        "ndx_narrative": ndx_narrative,
        "week_narrative": week_narrative,
        "pnl": pnl,
        "pnl_total_value": total_value,
        "pnl_total_overnight": total_overnight,
    }

    html = build_html(data)

    os.makedirs("output", exist_ok=True)
    today = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"output/morning_prep_{today}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Dashboard saved: {filename}")
    try:
        webbrowser.open(f"file://{os.path.abspath(filename)}")
        print("→ Opening in browser...")
    except Exception:
        print(f"   (open manually: {filename})")


if __name__ == "__main__":
    main()
