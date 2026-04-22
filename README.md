# Morning Prep Dashboard v2

## What's new vs v1

- **Four tabs now:** ASX 200, Nasdaq 100, My P&L, Week Ahead
- **Sector heatmap** for both markets — colour-coded grid showing sector winners/losers at a glance
- **Thesis-drift alerts** — each watchlist stock has a 1-line thesis; Claude flags when news contradicts it (highlighted in yellow)
- **P&L tracker** — configure your actual holdings in HOLDINGS at the top of the script, see overnight P&L and total P&L per position
- **Earnings calendar** — upcoming earnings for watchlist stocks (🔴 today, 🟡 within 2 days, ⚪ this week)
- **Economic calendar** — key data releases via ForexFactory
- **Week Ahead tab** — forward-looking Claude narrative on earnings, central banks, and macro themes
- **Robust ASX announcements** — direct scrape from asx.com.au with RSS fallback
- **Stale data handling** — flags tickers with missing or >1d old data rather than showing fake zeros
- **Dark mode toggle** — top-right button

## Run it

```bash
pip3 install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python3 morning_prep.py
```

Dashboard auto-opens in your browser.

## Customise

Open the script and edit the lists at the top:
- `ASX_WATCHLIST` — tuples of (ticker, thesis). Thesis drives the drift-detection logic.
- `NDX_WATCHLIST` — same format for US names.
- `HOLDINGS` — tuples of (ticker, shares, avg_cost) for your P&L tab.
- `ASX_SECTOR_ETFS` / `NDX_SECTOR_ETFS` — sector ETF tickers for the heatmap.
