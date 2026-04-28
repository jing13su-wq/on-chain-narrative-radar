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
