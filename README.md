# BharatAlpha — Free Daily Auto-Updating Setup (iPhone + Google Sites)

This kit makes **BharatAlpha** update its NSE/BSE prices and Indian market news **automatically every weekday at 4:00 PM IST** for ₹0 using GitHub Pages + GitHub Actions.

## 5-Minute Setup
1. **Create a free GitHub account** at `github.com` and click **New Repository** (name it `bharat-alpha`, set it to **Public**).
2. **Upload all files from this ZIP** into the root of that repository (keeping the `.github/workflows/daily_update.yml` and `scripts/update_daily_data.py` folders intact).
3. **Turn on GitHub Pages:** Go to **Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main` / `(root)` → Save**.
   - Within 1–2 minutes, your live app URL will be: `https://<your-username>.github.io/bharat-alpha/`
4. **Enable Daily Auto-Updates:** Go to the **Actions** tab in your repo, enable workflows if prompted, and click **Daily NSE & News Auto-Update → Run workflow** once to test it immediately. From then on, it runs automatically Mon–Fri at 4:00 PM IST.
5. **Use on iPhone or Google Sites:**
   - **iPhone (Best experience):** Open your `github.io` link in **Safari → Share → Add to Home Screen**.
   - **Google Sites:** Click **Insert → Embed → By URL**, paste your `github.io` link, and publish. Every time GitHub Actions updates `daily_data.json` (or you upload a new `index.html`), your Google Site updates automatically!
