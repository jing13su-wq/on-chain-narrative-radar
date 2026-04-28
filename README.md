# On-Chain Narrative Radar

Static on-chain token discovery dashboard for ETH, SOL, BSC, and Base.

## Data

The app reads from the public DEX Screener API in the browser:

- `/token-profiles/latest/v1`
- `/token-boosts/latest/v1`
- `/token-boosts/top/v1`
- `/community-takeovers/latest/v1`
- `/tokens/v1/{chainId}/{tokenAddresses}`

No API key is required for the current build.

## Local Preview

```powershell
cd D:\eepic\on-chain-narrative-radar
python -m http.server 4173
```

Open `http://localhost:4173/`.

## Static Deploy

Upload the contents of this folder to the desired static path, for example:

```text
connectfarm1.com/on-chain-narrative-radar/
```

For Cloudflare Pages, Netlify, Vercel static output, or a plain Nginx/Apache site, use this directory as the publish directory. There is no build step.

## Telegram Monitor

The monitor is a zero-dependency Python script that scans DEX Screener, scores tokens, deduplicates alerts, and posts fresh signals to Telegram.

Dry run:

```powershell
python .\monitor.py --once --dry-run --min-score 55
```

Required environment variables for real Telegram sends:

```powershell
$env:TELEGRAM_BOT_TOKEN="123456789:your_bot_token"
$env:TELEGRAM_CHAT_ID="your_chat_id"
python .\monitor.py --once
```

Continuous local loop:

```powershell
python .\monitor.py --loop --interval-minutes 10 --min-score 55
```

The script stores dedupe state in `.monitor_state.json` and suppresses repeat alerts for 24 hours by default.

## GitHub Actions Monitor

The repository includes `.github/workflows/monitor.yml`, which runs the monitor every 10 minutes.

Add these repository secrets before enabling real sends:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

GitHub path:

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

After both secrets are present, open `Actions -> On-chain narrative monitor -> Run workflow` to test one scan.

Manual workflow inputs:

- `dry_run=true`: preview messages without sending Telegram alerts.
- `reset_state=true`: ignore the cached dedupe state for this run.

For a real end-to-end Telegram test, run with `dry_run=false` and `reset_state=true`.

## GitHub Actions Market Kline Radar

The repository also includes `.github/workflows/market-kline-radar.yml`, which runs the market K-line radar every 5 minutes.

It scans Binance USDT perpetuals, renders plain candlestick charts, and sends chart images to Telegram.

Use the same repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Manual workflow inputs:

- `dry_run=true`: render charts without sending Telegram messages.
- `reset_state=true`: ignore cached rank and dedupe state.
- `bootstrap_volume_alerts=true`: alert current volume leaders even when no previous leaderboard exists.
- `interval`: `5m`, `15m`, or `1h`.
- `chart_limit`: `120`, `180`, or `240` candles per chart.
- `test_symbol`: send one chart for a specific symbol, for example `SOLUSDT`, bypassing filters and state.

The action stores its state in the GitHub Actions cache, so the "newly entered volume leaderboard" logic can compare against the previous scheduled run.
