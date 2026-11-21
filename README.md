# BharatAlpha — All-in-One Auto-Update Kit (NSE + BSE + Live News)

## What's Inside
- `index.html` — Full BharatAlpha web app (News + Live Refresh, FPI Flows, All NSE & BSE Directory + CSV Auto-Price Importer, SIP/Gold/Goal Planner, Trade Basket & STCG/LTCG Calculator).
- `scripts/update_daily_data.py` — Downloads official NSE `EQUITY_L.csv`, NSE Daily Full Bhavcopy, BSE UDiFF Equity Bhavcopy (~5,000+ merged NSE & BSE listed shares), and up to 20 headlines from ET Markets, Livemint, and Moneycontrol.
- `.github/workflows/daily_update.yml` — Runs automatically 3x EVERY day (Mon–Sun) at 8:30 AM, 1:00 PM, and 4:05 PM IST.
- `daily_data.json`, `market_all_stocks.json`, `my_holdings.json` — Starter data files auto-updated by GitHub Actions.

## Quick Setup on GitHub
1. Upload `index.html`, `daily_data.json`, `market_all_stocks.json`, `my_holdings.json`, and the `scripts` folder to your repo.
2. Ensure `.github/workflows/daily_update.yml` is inside `.github/workflows/` (use **Add file → Create new file** if your browser hides `.github`).
3. Go to **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**, and click **Save**.
4. Go to **Actions → Daily NSE, BSE & Market News Auto-Update → Run workflow** to trigger your first full update.
