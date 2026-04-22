#!/usr/bin/env python3
"""
Morning Prep Dashboard — ASX 200 + Nasdaq 100
──────────────────────────────────────────────
Builds a single HTML dashboard covering:
  • ASX 200 tab: overnight context, futures/implied open, ASX announcements,
    cap rates / RE, rates & fixed income, ASX macro flags, M&A & capital raises
  • Nasdaq 100 tab: overnight tech moves, earnings today, AI/semi names,
    futures, notable broker actions

Run locally each morning:
    python morning_prep.py
Opens output/morning_prep_YYYYMMDD.html in your browser.

Author: Sam Hash, 2026
"""

import os
import re
import sys
import feedparser
import yfinance as yf
import anthropic
import pytz
from datetime import datetime
from dataclasses import dataclass
from bs4 import BeautifulSoup
import requests
import webbrowser


# ── CONFIG ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0)"}

# Tickers to fetch price/change data for (Yahoo Finance format)
INDEX_TICKERS = {
    "ASX 200":          "^AXJO",
    "ASX 200 Futures":  "AP=F",     # SPI 200 futures
    "S&P 500":          "^GSPC",
    "Nasdaq 100":       "^NDX",
    "Nasdaq Futures":   "NQ=F",
    "Dow Jones":        "^DJI",
    "VIX":              "^VIX",
    "Nikkei 225":       "^N225",
    "Hang Seng":        "^HSI",
    "AUD/USD":          "AUDUSD=X",
    "Gold":             "GC=F",
    "WTI Crude":        "CL=F",
    "Iron Ore (62%)":   "TIO=F",
    "Copper":           "HG=F",
    "US 10Y Yield":     "^TNX",
    "AU 10Y Yield":     "AGB10YR=RR",  # may be spotty — fallback handled
}

# Watchlist stocks — customise freely
ASX_WATCHLIST = [
    "CBA.AX", "BHP.AX", "CSL.AX", "NAB.AX", "WBC.AX",
    "MQG.AX", "CHC.AX",  # Charter Hall
    "GMG.AX",            # Goodman
    "DXS.AX", "GPT.AX", "SCG.AX",  # REITs
    "WES.AX", "WOW.AX", "FMG.AX", "RIO.AX", "WDS.AX",
]

NDX_WATCHLIST = [
    "NVDA", "AMD", "TSM", "AAPL", "MSFT", "GOOGL",
    "META", "AMZN", "TSLA", "AVGO", "ASML", "ORCL",
    "PLTR", "CRWD", "NFLX",
]

# RSS feeds by section
FEEDS = {
    "asx_announcements": [
        ("ASX Announcements",    "https://www.asx.com.au/asx/1/company/announcements.rss"),
    ],
    "au_macro": [
        ("RBA Media Releases",   "https://www.rba.gov.au/rss/rss-cb-media-releases.xml"),
        ("Reuters Australia",    "https://feeds.reuters.com/reuters/AUdomesticNews"),
        ("ABS Releases",         "https://www.abs.gov.au/about/media-centre/media-releases/feed"),
    ],
    "rates_fi": [
        ("Reuters Bonds",        "https://feeds.reuters.com/reuters/bondsNews"),
        ("MarketWatch Bonds",    "https://feeds.marketwatch.com/marketwatch/bondmarket/"),
        ("Fed Press Releases",   "https://www.federalreserve.gov/feeds/press_all.xml"),
    ],
    "property_re": [
        ("The Urban Developer",  "https://theurbandeveloper.com/feed"),
        ("AU Property Journal",  "https://www.apijournal.com.au/feed/"),
        ("Property Council AU",  "https://www.propertycouncil.com.au/rss"),
    ],
    "ma_deals": [
        ("Reuters M&A",          "https://feeds.reuters.com/reuters/mergersNews"),
        ("Reuters IPO",          "https://feeds.reuters.com/reuters/IPOsnews"),
    ],
    "tech_ai": [
        ("CNBC Tech",            "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
        ("Reuters Tech",         "https://feeds.reuters.com/reuters/technologyNews"),
        ("Seeking Alpha Tech",   "https://seekingalpha.com/tag/tech.xml"),
    ],
    "us_markets": [
        ("Reuters Markets",      "https://feeds.reuters.com/reuters/marketsNews"),
        ("CNBC Markets",         "https://www.cnbc.com/id/20409666/device/rss/rss.html"),
        ("MarketWatch Markets",  "https://feeds.marketwatch.com/marketwatch/marketpulse/"),
    ],
}


# ── DATA FETCHING ─────────────────────────────────────────────────────────
def fetch_prices():
    """Pull index and FX/commodity data from yfinance."""
    data = {}
    for name, ticker in INDEX_TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            info = t.history(period="2d")
            if len(info) >= 2:
                last  = info["Close"].iloc[-1]
                prev  = info["Close"].iloc[-2]
                change = last - prev
                pct    = (change / prev) * 100 if prev else 0
                data[name] = {
                    "last":   round(last, 2),
                    "change": round(change, 2),
                    "pct":    round(pct, 2),
                }
            elif len(info) == 1:
                data[name] = {"last": round(info["Close"].iloc[-1], 2), "change": 0, "pct": 0}
        except Exception as e:
            data[name] = {"last": "—", "change": 0, "pct": 0, "err": str(e)[:60]}
    return data


def fetch_watchlist(tickers: list[str]) -> list[dict]:
    """Fetch price + % change for each stock."""
    rows = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) >= 2:
                last = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                pct  = ((last - prev) / prev) * 100
                rows.append({
                    "ticker": ticker.replace(".AX", ""),
                    "last":   round(last, 2),
                    "pct":    round(pct, 2),
                })
        except Exception:
            rows.append({"ticker": ticker, "last": "—", "pct": 0})
    return rows


def fetch_feed(name: str, url: str, max_items: int = 5) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        out = []
        for entry in feed.entries[:max_items]:
            title   = entry.get("title", "").strip()
            summary = re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()[:400]
            link    = entry.get("link", "")
            pub     = entry.get("published", "")[:16]
            out.append({"source": name, "title": title, "summary": summary, "link": link, "pub": pub})
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
def ask_claude(prompt: str, max_tokens: int = 1500) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def generate_asx_narrative(prices: dict, watchlist: list[dict], feeds: dict) -> str:
    feed_text = "\n\n".join(
        f"[{section.upper()}]\n" + "\n".join(f"• {i['title']} ({i['source']})\n  {i['summary'][:200]}"
                                              for i in items[:10])
        for section, items in feeds.items()
    )

    prices_text = "\n".join(f"{k}: {v.get('last')} ({v.get('pct', 0):+.2f}%)" for k, v in prices.items())
    watchlist_text = "\n".join(f"{r['ticker']}: {r['last']} ({r['pct']:+.2f}%)" for r in watchlist)

    prompt = f"""You are producing the morning market prep section for an Australian equities analyst. Today is {datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y")}.

Write ~400 words covering ASX-relevant context for today's open. Structure it with the following mini-sections, written as short punchy paragraphs (no bullets):

**OVERNIGHT SETUP** — US/EU close, SPI futures, likely ASX open tone.
**RATES & FIXED INCOME** — US 10Y moves, RBA commentary if any, AU bond implications.
**CAP RATES & REAL ESTATE** — commercial property, REIT moves, industrial/logistics focus.
**ASX MACRO FLAGS** — any data due today, RBA speakers, political/regulatory developments.
**M&A & CAPITAL RAISES** — notable Australian deal activity, anything live in the market.
**STOCKS TO WATCH** — from the watchlist, flag 2-3 names with specific catalysts today.

Tight, analyst-grade prose. No fluff. No headers repeated — use bold for the mini-section labels.

--- INDEX & MACRO DATA ---
{prices_text}

--- ASX WATCHLIST ---
{watchlist_text}

--- NEWS FEEDS ---
{feed_text}
"""
    return ask_claude(prompt, max_tokens=1800)


def generate_ndx_narrative(prices: dict, watchlist: list[dict], feeds: dict) -> str:
    feed_text = "\n\n".join(
        f"[{section.upper()}]\n" + "\n".join(f"• {i['title']} ({i['source']})\n  {i['summary'][:200]}"
                                              for i in items[:10])
        for section, items in feeds.items()
    )

    prices_text = "\n".join(f"{k}: {v.get('last')} ({v.get('pct', 0):+.2f}%)" for k, v in prices.items())
    watchlist_text = "\n".join(f"{r['ticker']}: {r['last']} ({r['pct']:+.2f}%)" for r in watchlist)

    prompt = f"""You are producing the morning market prep section for a Nasdaq 100 / US tech-focused analyst. Today is {datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y")}.

Write ~400 words covering Nasdaq 100 context for today. Structure with bold mini-section labels (no headers, punchy paragraphs):

**OVERNIGHT CLOSE** — Nasdaq + mega-caps, breadth, sector leadership, futures implied open.
**RATES & TECH** — 10Y yield dynamics and tech multiple implications.
**AI & SEMICONDUCTORS** — NVDA, AMD, TSM, AVGO — any news, supply chain, capex commentary.
**EARNINGS & GUIDANCE** — companies reporting today/tonight, pre-market movers from earnings.
**M&A & DEAL FLOW** — tech M&A activity, notable private deals.
**NAMES TO WATCH** — 2-3 specific stocks from the watchlist with catalysts today.

Tight, punchy, analyst-grade. No fluff.

--- PRICE DATA ---
{prices_text}

--- WATCHLIST ---
{watchlist_text}

--- NEWS FEEDS ---
{feed_text}
"""
    return ask_claude(prompt, max_tokens=1800)


# ── HTML BUILDER ──────────────────────────────────────────────────────────
def price_cell(v: dict) -> str:
    pct = v.get("pct", 0)
    colour = "#0a7a0a" if pct > 0 else ("#c62828" if pct < 0 else "#666")
    arrow  = "▲" if pct > 0 else ("▼" if pct < 0 else "—")
    return f'<span style="color:{colour};font-weight:600;">{arrow} {pct:+.2f}%</span>'


def build_price_table(prices: dict, keys: list[str]) -> str:
    rows = []
    for k in keys:
        if k in prices:
            v = prices[k]
            last = v.get("last", "—")
            rows.append(f"<tr><td>{k}</td><td style='text-align:right;'>{last}</td><td style='text-align:right;'>{price_cell(v)}</td></tr>")
    return "<table class='price-table'><tr><th>Market</th><th style='text-align:right;'>Level</th><th style='text-align:right;'>Chg</th></tr>" + "".join(rows) + "</table>"


def build_watchlist_table(rows: list[dict]) -> str:
    tr = []
    for r in rows:
        pct = r.get("pct", 0)
        colour = "#0a7a0a" if pct > 0 else ("#c62828" if pct < 0 else "#666")
        tr.append(f"<tr><td><b>{r['ticker']}</b></td><td style='text-align:right;'>{r['last']}</td><td style='text-align:right;color:{colour};font-weight:600;'>{pct:+.2f}%</td></tr>")
    return "<table class='price-table'><tr><th>Ticker</th><th style='text-align:right;'>Last</th><th style='text-align:right;'>%</th></tr>" + "".join(tr) + "</table>"


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
    return "".join(html)


def narrative_to_html(text: str) -> str:
    """Convert **bold** labels to HTML and format paragraphs."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    out = []
    for p in paragraphs:
        # bold conversion
        p = re.sub(r"\*\*(.+?)\*\*", r'<strong class="mini-label">\1</strong>', p)
        out.append(f"<p>{p}</p>")
    return "".join(out)


def build_html(data: dict) -> str:
    asx_prices = data["prices"]
    now_str = datetime.now(pytz.timezone("Australia/Sydney")).strftime("%A, %d %B %Y · %H:%M AEST")

    asx_market_keys = ["ASX 200", "ASX 200 Futures", "S&P 500", "Nasdaq 100", "VIX", "Nikkei 225", "Hang Seng"]
    asx_macro_keys  = ["AUD/USD", "US 10Y Yield", "Gold", "WTI Crude", "Iron Ore (62%)", "Copper"]
    ndx_market_keys = ["Nasdaq 100", "Nasdaq Futures", "S&P 500", "Dow Jones", "VIX"]
    ndx_macro_keys  = ["US 10Y Yield", "Gold", "WTI Crude", "AUD/USD"]

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Morning Prep — {now_str}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #1a1a1a; margin: 0; padding: 0; background: #f5f5f5; }}
  .container {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
  .topbar {{ background: #0b3d91; color: white; padding: 18px 24px; border-radius: 6px 6px 0 0; }}
  .topbar h1 {{ margin: 0; font-size: 22px; letter-spacing: 0.3px; }}
  .topbar .sub {{ font-size: 12px; opacity: 0.85; margin-top: 2px; }}
  .tabs {{ display: flex; background: white; border-bottom: 2px solid #0b3d91; }}
  .tab {{ padding: 14px 28px; cursor: pointer; font-weight: 600; font-size: 14px; color: #555; border-bottom: 3px solid transparent; }}
  .tab.active {{ color: #0b3d91; border-bottom: 3px solid #0b3d91; background: #f5f8fd; }}
  .tab-content {{ display: none; background: white; padding: 24px; border-radius: 0 0 6px 6px; }}
  .tab-content.active {{ display: block; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 22px; margin-bottom: 22px; }}
  .grid-three {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 22px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: #0b3d91; margin: 0 0 10px 0; padding-bottom: 6px; border-bottom: 1px solid #e0e0e0; }}
  .card {{ background: #fafafa; border: 1px solid #e8e8e8; border-radius: 5px; padding: 16px 18px; }}
  .price-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .price-table th {{ text-align: left; font-size: 10.5px; text-transform: uppercase; color: #888; letter-spacing: 0.5px; padding: 6px 8px; border-bottom: 1px solid #ddd; }}
  .price-table td {{ padding: 7px 8px; border-bottom: 1px solid #f0f0f0; font-size: 13px; }}
  .narrative {{ background: #f5f8fd; border-left: 4px solid #0b3d91; padding: 18px 22px; border-radius: 4px; font-size: 14px; line-height: 1.7; }}
  .narrative p {{ margin: 10px 0; }}
  .narrative .mini-label {{ display: inline; color: #0b3d91; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; margin-right: 6px; }}
  .news-item {{ padding: 10px 0; border-bottom: 1px solid #eee; }}
  .news-item:last-child {{ border-bottom: none; }}
  .news-title {{ font-size: 13.5px; font-weight: 600; margin-bottom: 2px; }}
  .news-title a {{ color: #1a1a1a; text-decoration: none; }}
  .news-title a:hover {{ color: #0b3d91; }}
  .news-meta {{ font-size: 10.5px; color: #888; margin-bottom: 4px; }}
  .news-summary {{ font-size: 12.5px; color: #555; line-height: 1.5; }}
  footer {{ text-align: center; color: #999; font-size: 10.5px; margin-top: 30px; padding: 18px; }}
</style>
</head>
<body>

<div class="container">

  <div class="topbar">
    <h1>📈 Morning Prep Dashboard</h1>
    <div class="sub">{now_str}</div>
  </div>

  <div class="tabs">
    <div class="tab active" onclick="showTab('asx')">ASX 200</div>
    <div class="tab" onclick="showTab('ndx')">Nasdaq 100</div>
  </div>

  <!-- ═══════════════════ ASX TAB ═══════════════════ -->
  <div id="tab-asx" class="tab-content active">

    <h2>Market Snapshot</h2>
    <div class="grid">
      <div class="card">
        <h2 style="border:none;padding:0;margin-bottom:6px;">Indices & Futures</h2>
        {build_price_table(asx_prices, asx_market_keys)}
      </div>
      <div class="card">
        <h2 style="border:none;padding:0;margin-bottom:6px;">FX, Rates & Commodities</h2>
        {build_price_table(asx_prices, asx_macro_keys)}
      </div>
    </div>

    <h2>Analyst Narrative</h2>
    <div class="narrative">{narrative_to_html(data["asx_narrative"])}</div>

    <h2 style="margin-top:28px;">Watchlist</h2>
    <div class="card">{build_watchlist_table(data["asx_watchlist"])}</div>

    <div class="grid-three" style="margin-top:28px;">
      <div>
        <h2>ASX Announcements</h2>
        {build_news_block(data["asx_feeds"]["asx_announcements"])}
      </div>
      <div>
        <h2>Rates & Fixed Income</h2>
        {build_news_block(data["asx_feeds"]["rates_fi"])}
      </div>
      <div>
        <h2>Property & RE</h2>
        {build_news_block(data["asx_feeds"]["property_re"])}
      </div>
    </div>

    <div class="grid" style="margin-top:28px;">
      <div>
        <h2>ASX Macro Flags</h2>
        {build_news_block(data["asx_feeds"]["au_macro"])}
      </div>
      <div>
        <h2>M&amp;A + Capital Raises</h2>
        {build_news_block(data["asx_feeds"]["ma_deals"])}
      </div>
    </div>

  </div>

  <!-- ═══════════════════ NDX TAB ═══════════════════ -->
  <div id="tab-ndx" class="tab-content">

    <h2>Market Snapshot</h2>
    <div class="grid">
      <div class="card">
        <h2 style="border:none;padding:0;margin-bottom:6px;">Indices & Futures</h2>
        {build_price_table(asx_prices, ndx_market_keys)}
      </div>
      <div class="card">
        <h2 style="border:none;padding:0;margin-bottom:6px;">Rates & Commodities</h2>
        {build_price_table(asx_prices, ndx_macro_keys)}
      </div>
    </div>

    <h2>Analyst Narrative</h2>
    <div class="narrative">{narrative_to_html(data["ndx_narrative"])}</div>

    <h2 style="margin-top:28px;">Watchlist</h2>
    <div class="card">{build_watchlist_table(data["ndx_watchlist"])}</div>

    <div class="grid" style="margin-top:28px;">
      <div>
        <h2>US Markets</h2>
        {build_news_block(data["ndx_feeds"]["us_markets"])}
      </div>
      <div>
        <h2>Tech & AI</h2>
        {build_news_block(data["ndx_feeds"]["tech_ai"])}
      </div>
    </div>

    <div class="grid" style="margin-top:28px;">
      <div>
        <h2>Rates & Fixed Income</h2>
        {build_news_block(data["ndx_feeds"]["rates_fi"])}
      </div>
      <div>
        <h2>M&amp;A + IPOs</h2>
        {build_news_block(data["ndx_feeds"]["ma_deals"])}
      </div>
    </div>

  </div>

  <footer>Built by Sam Hash · Generated by Claude · Sources: Yahoo Finance, Reuters, CNBC, MarketWatch, ASX, RBA, The Urban Developer, Australian Property Journal</footer>

</div>

<script>
function showTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}}
</script>
</body>
</html>
"""
    return html


# ── MAIN ──────────────────────────────────────────────────────────────────
def main():
    print("→ Fetching prices from Yahoo Finance...")
    prices = fetch_prices()

    print("→ Fetching ASX watchlist...")
    asx_watchlist = fetch_watchlist(ASX_WATCHLIST)

    print("→ Fetching Nasdaq watchlist...")
    ndx_watchlist = fetch_watchlist(NDX_WATCHLIST)

    print("→ Gathering news feeds...")
    asx_feeds = {
        "asx_announcements": gather_feed_section(["asx_announcements"]),
        "au_macro":          gather_feed_section(["au_macro"]),
        "rates_fi":          gather_feed_section(["rates_fi"]),
        "property_re":       gather_feed_section(["property_re"]),
        "ma_deals":          gather_feed_section(["ma_deals"]),
    }
    ndx_feeds = {
        "us_markets": gather_feed_section(["us_markets"]),
        "tech_ai":    gather_feed_section(["tech_ai"]),
        "rates_fi":   gather_feed_section(["rates_fi"]),
        "ma_deals":   gather_feed_section(["ma_deals"]),
    }

    print("→ Generating ASX narrative with Claude...")
    asx_narrative = generate_asx_narrative(prices, asx_watchlist, asx_feeds)

    print("→ Generating Nasdaq narrative with Claude...")
    ndx_narrative = generate_ndx_narrative(prices, ndx_watchlist, ndx_feeds)

    print("→ Building HTML dashboard...")
    data = {
        "prices":         prices,
        "asx_watchlist":  asx_watchlist,
        "ndx_watchlist":  ndx_watchlist,
        "asx_feeds":      asx_feeds,
        "ndx_feeds":      ndx_feeds,
        "asx_narrative":  asx_narrative,
        "ndx_narrative":  ndx_narrative,
    }
    html = build_html(data)

    os.makedirs("output", exist_ok=True)
    today    = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"output/morning_prep_{today}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Dashboard saved: {filename}")
    print("→ Opening in default browser...")

    # Try to open in browser
    try:
        webbrowser.open(f"file://{os.path.abspath(filename)}")
    except Exception:
        print(f"   (could not auto-open — open manually: {filename})")


if __name__ == "__main__":
    main()
