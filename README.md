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
