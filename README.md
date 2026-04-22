# Morning Prep Dashboard — ASX 200 + Nasdaq 100

## What it does
Builds a single HTML dashboard you open each morning over coffee. Two tabs:

**ASX 200 tab**
- Market snapshot: ASX 200, futures, S&P 500, Nasdaq, VIX, Nikkei, Hang Seng
- FX, rates & commodities: AUD/USD, US 10Y, Gold, WTI, Iron Ore, Copper
- Claude-generated analyst narrative (overnight setup, rates, cap rates & RE, ASX macro flags, M&A, stocks to watch)
- Your watchlist with overnight % moves
- ASX announcements, Rates news, Property news, Macro flags, M&A deals

**Nasdaq 100 tab**
- Market snapshot focused on US close
- Claude-generated narrative (overnight close, rates & tech, AI/semis, earnings, M&A, names to watch)
- Your Nasdaq watchlist
- US markets news, Tech & AI, Rates, M&A

---

## Run it

### Install dependencies
```bash
pip3 install -r requirements.txt
```

### Set API key
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Run
```bash
python3 morning_prep.py
```

The script:
1. Fetches live prices via Yahoo Finance
2. Scrapes all the RSS feeds
3. Generates analyst narratives via Claude
4. Builds the HTML file
5. Auto-opens it in your default browser

Takes about 60-90 seconds. Output lives in `output/morning_prep_YYYYMMDD_HHMM.html`.

---

## Customising

### Change watchlists
Edit the top of `morning_prep.py`:
```python
ASX_WATCHLIST = ["CBA.AX", "BHP.AX", ...]
NDX_WATCHLIST = ["NVDA", "AMD", ...]
```
Yahoo format: `.AX` suffix for ASX, plain ticker for US.

### Add feeds
Add entries to the `FEEDS` dict.

### Add indices/commodities
Add to `INDEX_TICKERS` dict. Find tickers at finance.yahoo.com.

---

## Daily workflow
1. Get out of bed
2. Open Terminal, run `python3 morning_prep.py`
3. Dashboard auto-opens in browser
4. Read over coffee before ASX open (10am Sydney)

---

## CV line
> *Built a Python-based morning prep dashboard integrating live Yahoo Finance data, RSS news aggregation, and Claude-generated analyst commentary — covers ASX 200 and Nasdaq 100 with custom watchlists, rates/FX/commodities, and M&A flow.*

## For interviews
Open your laptop, show them the dashboard. It's hard to argue with someone who clearly does their own morning prep and built the tool to do it.
