# Market Kline Radar

交易所短线扫盘器：只使用交易所价格与成交额数据，筛选有热度的 USDT 永续币种，生成纯 K 线图并推送到 Telegram bot。

## 当前信号

1. **成交额榜新进币**
   - 当前进入 24h 成交额前 `--volume-top-n`
   - 上一次扫描不在这个榜单里
   - 24h 成交额不低于 `--min-volume-quote`

2. **有成交额支撑的涨幅榜币**
   - 24h 涨幅不低于 `--min-gain-pct`
   - 24h 成交额不低于 `--min-gainer-volume-quote`
   - 取涨幅榜前 `--gainer-top-n`

推送内容默认只有 K 线图和极简标题，不包含 OI、费率、均线、RSI、MACD 等任何指标。

## 本地测试

```powershell
cd D:\eepic
python .\market_kline_radar\scanner.py --once --dry-run
```

首次运行时，成交额榜新进币需要先建立“上一轮成交额榜”基线，所以通常只会看到涨幅榜候选。想让首次运行也对当前成交额榜出图，可以加：

```powershell
python .\market_kline_radar\scanner.py --once --dry-run --bootstrap-volume-alerts
```

## Telegram 推送

```powershell
$env:TELEGRAM_BOT_TOKEN="123456789:your_bot_token"
$env:TELEGRAM_CHAT_ID="your_chat_id"
python .\market_kline_radar\scanner.py --once
```

连续运行：

```powershell
python .\market_kline_radar\scanner.py --loop --interval-minutes 5
```

## 常用参数

- `--interval`: K 线周期，默认 `15m`
- `--chart-limit`: K 线根数，默认 `180`
- `--candle-width-scale`: K 线实体宽度，默认 `0.48`；想更密可降到 `0.35`
- `--volume-top-n`: 成交额榜前 N，默认 `40`
- `--gainer-top-n`: 涨幅榜前 N，默认 `25`
- `--min-volume-quote`: 成交额榜信号的最低 24h 成交额，默认 `50000000`
- `--min-gainer-volume-quote`: 涨幅榜信号的最低 24h 成交额，默认 `20000000`
- `--min-gain-pct`: 涨幅榜最低 24h 涨幅，默认 `12`
- `--seen-ttl-hours`: 同一币种同一触发类型的重复推送抑制时间，默认 `6`
- `--max-alerts`: 每轮最多推送图片数，默认 `8`

## 设计取舍

- 默认交易所为 Binance USDT 永续，适合短线流动性筛选。
- 默认排除 BTC、ETH 与稳定币基底，减少噪音。
- 成交额榜“之前不在榜”以本地 state 文件的上一轮榜单为基准，文件默认是 `market_kline_radar/state.json`。
- 图上只画 K 线，不叠加成交量柱或技术指标。
