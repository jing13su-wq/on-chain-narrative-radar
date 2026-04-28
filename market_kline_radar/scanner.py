#!/usr/bin/env python3
"""Exchange market kline radar with Telegram chart pushes.

The scanner deliberately uses only exchange price/volume data. It finds:

- symbols that newly enter the 24h quote-volume leaderboard;
- top gainers with enough 24h quote volume.

For each fresh candidate it renders a plain candlestick chart and optionally
sends the image to Telegram.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests
from matplotlib.patches import Rectangle


BINANCE_FAPI = "https://fapi.binance.com"
STATE_VERSION = 1
STABLE_BASES = {
    "USDC",
    "FDUSD",
    "TUSD",
    "BUSD",
    "DAI",
    "USDP",
    "EUR",
    "TRY",
    "BRL",
}
DEFAULT_EXCLUDED_BASES = {"BTC", "ETH"} | STABLE_BASES


@dataclass(frozen=True)
class Ticker:
    symbol: str
    base_asset: str
    quote_volume: float
    price_change_pct: float
    last_price: float


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Candidate:
    symbol: str
    reason: str
    rank: int


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def request_json(path: str, params: dict[str, Any] | None = None, timeout: int = 20) -> Any:
    response = requests.get(
        f"{BINANCE_FAPI}{path}",
        params=params,
        headers={
            "Accept": "application/json",
            "User-Agent": "market-kline-radar/0.1",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "volume_top": [], "seen": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": STATE_VERSION, "volume_top": [], "seen": {}}
    state.setdefault("version", STATE_VERSION)
    state.setdefault("volume_top", [])
    state.setdefault("seen", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def prune_seen(state: dict[str, Any], ttl_hours: float) -> None:
    cutoff = int(time.time()) - int(ttl_hours * 3600)
    seen = state.setdefault("seen", {})
    for key in list(seen):
        if int(seen[key].get("last_alerted_at", 0)) < cutoff:
            del seen[key]


def base_asset(symbol: str, quote: str = "USDT") -> str:
    return symbol[: -len(quote)] if symbol.endswith(quote) else symbol


def tradable_usdt_perps(excluded_bases: set[str]) -> dict[str, str]:
    data = request_json("/fapi/v1/exchangeInfo")
    symbols: dict[str, str] = {}
    for item in data.get("symbols", []):
        if item.get("contractType") != "PERPETUAL":
            continue
        if item.get("quoteAsset") != "USDT" or item.get("status") != "TRADING":
            continue
        base = str(item.get("baseAsset") or base_asset(item["symbol"]))
        if base in excluded_bases:
            continue
        symbols[item["symbol"]] = base
    return symbols


def tickers(excluded_bases: set[str]) -> list[Ticker]:
    allowed = tradable_usdt_perps(excluded_bases)
    rows = request_json("/fapi/v1/ticker/24hr")
    result = []
    for row in rows:
        symbol = row.get("symbol")
        if symbol not in allowed:
            continue
        try:
            result.append(
                Ticker(
                    symbol=symbol,
                    base_asset=allowed[symbol],
                    quote_volume=float(row.get("quoteVolume") or 0),
                    price_change_pct=float(row.get("priceChangePercent") or 0),
                    last_price=float(row.get("lastPrice") or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


def select_candidates(
    rows: list[Ticker],
    state: dict[str, Any],
    volume_top_n: int,
    gainer_top_n: int,
    min_volume_quote: float,
    min_gainer_volume_quote: float,
    min_gain_pct: float,
    bootstrap_volume_alerts: bool,
) -> tuple[list[Candidate], list[str]]:
    volume_ranked = sorted(rows, key=lambda item: item.quote_volume, reverse=True)
    current_volume_top = [item.symbol for item in volume_ranked[:volume_top_n]]
    previous_volume_top = set(state.get("volume_top") or [])

    candidates: dict[tuple[str, str], Candidate] = {}
    if previous_volume_top or bootstrap_volume_alerts:
        for rank, item in enumerate(volume_ranked[:volume_top_n], start=1):
            if item.quote_volume < min_volume_quote:
                continue
            if item.symbol not in previous_volume_top:
                candidates[(item.symbol, "volume_new")] = Candidate(item.symbol, "volume_new", rank)

    gainer_ranked = sorted(rows, key=lambda item: item.price_change_pct, reverse=True)
    for rank, item in enumerate(gainer_ranked, start=1):
        if rank > gainer_top_n:
            break
        if item.price_change_pct < min_gain_pct:
            continue
        if item.quote_volume < min_gainer_volume_quote:
            continue
        candidates[(item.symbol, "gainer")] = Candidate(item.symbol, "gainer", rank)

    ordered = sorted(
        candidates.values(),
        key=lambda item: (0 if item.reason == "volume_new" else 1, item.rank, item.symbol),
    )
    return ordered, current_volume_top


def fresh_candidates(candidates: list[Candidate], state: dict[str, Any], max_alerts: int) -> list[Candidate]:
    seen = state.setdefault("seen", {})
    fresh = [item for item in candidates if f"{item.symbol}:{item.reason}" not in seen]
    return fresh[:max_alerts]


def mark_seen(candidates: list[Candidate], state: dict[str, Any]) -> None:
    stamp = int(time.time())
    seen = state.setdefault("seen", {})
    for item in candidates:
        seen[f"{item.symbol}:{item.reason}"] = {
            "last_alerted_at": stamp,
            "symbol": item.symbol,
            "reason": item.reason,
            "rank": item.rank,
        }


def fetch_klines(symbol: str, interval: str, limit: int) -> list[Candle]:
    rows = request_json(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )
    candles = []
    for row in rows:
        candles.append(
            Candle(
                open_time=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
            )
        )
    return candles


def interval_to_minutes(interval: str) -> int:
    unit = interval[-1]
    value = int(interval[:-1])
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    if unit == "d":
        return value * 24 * 60
    raise ValueError(f"unsupported interval: {interval}")


def nice_price_format(value: float) -> str:
    if value >= 100:
        return f"{value:,.1f}"
    if value >= 1:
        return f"{value:,.3f}"
    if value >= 0.01:
        return f"{value:,.5f}"
    return f"{value:,.8f}"


def render_candles(symbol: str, candles: list[Candle], interval: str, out_dir: Path, candle_width_scale: float) -> Path:
    if len(candles) < 2:
        raise ValueError(f"not enough candles for {symbol}")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{symbol}_{interval}_{int(time.time())}.png"
    times = [datetime.fromtimestamp(c.open_time / 1000, tz=timezone.utc) for c in candles]
    x_values = mdates.date2num(times)
    width = (interval_to_minutes(interval) / (24 * 60)) * candle_width_scale

    fig, ax = plt.subplots(figsize=(11.5, 6.2), dpi=160)
    fig.patch.set_facecolor("#0b0f14")
    ax.set_facecolor("#0b0f14")

    up_color = "#24c486"
    down_color = "#f45b69"
    neutral_color = "#c9d1d9"

    for x, candle in zip(x_values, candles):
        color = up_color if candle.close >= candle.open else down_color
        ax.vlines(x, candle.low, candle.high, color=color, linewidth=1.15, alpha=0.95)
        lower = min(candle.open, candle.close)
        height = abs(candle.close - candle.open)
        if math.isclose(height, 0.0):
            ax.hlines(candle.close, x - width / 2, x + width / 2, color=neutral_color, linewidth=1.1)
        else:
            rect = Rectangle(
                (x - width / 2, lower),
                width,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
                alpha=0.95,
            )
            ax.add_patch(rect)

    latest = candles[-1].close
    ax.set_title(f"{symbol}  {interval}  {utc_stamp()}", color="#e6edf3", fontsize=13, pad=14)
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.yaxis.set_major_formatter(lambda value, _pos: nice_price_format(value))
    ax.tick_params(axis="both", colors="#9aa4af", labelsize=9)
    ax.grid(True, color="#27313a", linewidth=0.6, alpha=0.7)
    for spine in ax.spines.values():
        spine.set_color("#27313a")

    ax.axhline(latest, color="#59636e", linewidth=0.8, alpha=0.85)
    ax.text(
        1.004,
        latest,
        nice_price_format(latest),
        transform=ax.get_yaxis_transform(),
        color="#e6edf3",
        fontsize=8,
        va="center",
        ha="left",
        bbox={"boxstyle": "round,pad=0.22", "facecolor": "#161b22", "edgecolor": "#30363d"},
    )

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=timezone.utc))
    ax.set_xlim(x_values[0] - width, x_values[-1] + width * 4)
    fig.tight_layout(pad=1.0)
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return path


def send_telegram_photo(token: str, chat_id: str, image_path: Path, caption: str, timeout: int = 30) -> None:
    with image_path.open("rb") as handle:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={
                "chat_id": chat_id,
                "caption": caption,
                "disable_notification": "false",
            },
            files={"photo": (image_path.name, handle, "image/png")},
            timeout=timeout,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Telegram non-JSON response: {response.text[:300]}") from exc
    if not response.ok or not payload.get("ok"):
        raise RuntimeError(f"Telegram sendPhoto failed: HTTP {response.status_code} {payload}")


def run_once(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file)
    chart_dir = Path(args.chart_dir)

    if args.test_symbol:
        fresh = [Candidate(args.test_symbol.upper(), "test", 1)]
        print(f"[{utc_stamp()}] test_symbol={fresh[0].symbol} interval={args.interval}")
    else:
        state = load_state(state_path)
        if args.reset_state:
            state = {"version": STATE_VERSION, "volume_top": [], "seen": {}}
        prune_seen(state, args.seen_ttl_hours)

        excluded_bases = {item.strip().upper() for item in args.exclude_bases.split(",") if item.strip()}
        rows = tickers(excluded_bases)
        candidates, current_volume_top = select_candidates(
            rows,
            state,
            volume_top_n=args.volume_top_n,
            gainer_top_n=args.gainer_top_n,
            min_volume_quote=args.min_volume_quote,
            min_gainer_volume_quote=args.min_gainer_volume_quote,
            min_gain_pct=args.min_gain_pct,
            bootstrap_volume_alerts=args.bootstrap_volume_alerts,
        )
        fresh = fresh_candidates(candidates, state, args.max_alerts)

        print(
            f"[{utc_stamp()}] tickers={len(rows)} candidates={len(candidates)} "
            f"fresh={len(fresh)} interval={args.interval}"
        )

        state["volume_top"] = current_volume_top
        state["volume_top_updated_at"] = int(time.time())

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not args.dry_run and fresh and (not token or not chat_id):
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or run with --dry-run.")

    sent_or_rendered: list[Candidate] = []
    for item in fresh:
        try:
            candles = fetch_klines(item.symbol, args.interval, args.chart_limit)
            image_path = render_candles(item.symbol, candles, args.interval, chart_dir, args.candle_width_scale)
            caption = f"{item.symbol} {args.interval} Kline"
            if args.dry_run:
                print(f"[dry-run] {item.symbol} reason={item.reason} rank={item.rank} chart={image_path}")
            else:
                send_telegram_photo(token, chat_id, image_path, caption)
                print(f"[sent] {item.symbol} reason={item.reason} rank={item.rank}")
            sent_or_rendered.append(item)
            time.sleep(args.sleep)
        except Exception as exc:  # noqa: BLE001 - keep the scan moving.
            print(f"[warn] {item.symbol} skipped: {exc}", file=sys.stderr)

    if args.test_symbol:
        print("[test] state not updated")
    elif args.dry_run:
        print("[dry-run] state not updated")
    else:
        mark_seen(sent_or_rendered, state)
        save_state(state_path, state)
    return 0


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan Binance USDT perpetuals and push pure K-line charts.")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    parser.add_argument("--loop", action="store_true", help="Keep scanning forever.")
    parser.add_argument("--dry-run", action="store_true", help="Render charts and print candidates without Telegram sends.")
    parser.add_argument("--interval-minutes", type=float, default=5.0, help="Loop interval in minutes.")
    parser.add_argument("--interval", default="15m", help="Kline interval, for example 5m, 15m, 1h.")
    parser.add_argument("--chart-limit", type=int, default=180, help="Number of candles per chart.")
    parser.add_argument("--candle-width-scale", type=float, default=0.48, help="Candle body width scale from 0.2 to 0.9.")
    parser.add_argument("--test-symbol", default="", help="Send one chart for this symbol, bypassing all filters and state.")
    parser.add_argument("--volume-top-n", type=int, default=40, help="24h quote-volume leaderboard size.")
    parser.add_argument("--gainer-top-n", type=int, default=25, help="24h gainer leaderboard size to inspect.")
    parser.add_argument("--min-volume-quote", type=float, default=50_000_000, help="Minimum 24h quote volume for volume-new signals.")
    parser.add_argument(
        "--min-gainer-volume-quote",
        type=float,
        default=20_000_000,
        help="Minimum 24h quote volume for gainer signals.",
    )
    parser.add_argument("--min-gain-pct", type=float, default=12.0, help="Minimum 24h gain percent for gainer signals.")
    parser.add_argument("--seen-ttl-hours", type=float, default=6.0, help="Suppress repeated symbol/reason alerts for this many hours.")
    parser.add_argument("--max-alerts", type=int, default=8, help="Maximum charts to push per scan.")
    parser.add_argument("--state-file", default="market_kline_radar/state.json", help="State JSON path.")
    parser.add_argument("--chart-dir", default="market_kline_radar/charts", help="Rendered chart output directory.")
    parser.add_argument("--reset-state", action="store_true", help="Clear local rank and dedupe state before scanning.")
    parser.add_argument(
        "--bootstrap-volume-alerts",
        action="store_true",
        help="Allow volume-new alerts even when no previous volume leaderboard exists.",
    )
    parser.add_argument("--sleep", type=float, default=0.25, help="Pause between chart fetches/sends.")
    parser.add_argument(
        "--exclude-bases",
        default=",".join(sorted(DEFAULT_EXCLUDED_BASES)),
        help="Comma-separated base assets to exclude.",
    )
    args = parser.parse_args(list(argv))
    if not args.once and not args.loop:
        args.once = True
    if args.chart_limit < 20:
        raise SystemExit("--chart-limit should be at least 20.")
    if not 0.2 <= args.candle_width_scale <= 0.9:
        raise SystemExit("--candle-width-scale should be between 0.2 and 0.9.")
    return args


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    if args.loop:
        while True:
            try:
                run_once(args)
            except Exception as exc:  # noqa: BLE001 - keep monitor alive.
                print(f"[error] {exc}", file=sys.stderr)
            time.sleep(max(30.0, args.interval_minutes * 60.0))
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())
