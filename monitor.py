#!/usr/bin/env python3
"""On-chain narrative monitor with Telegram alerts.

The monitor uses the public DEX Screener API, scores fresh token profiles,
deduplicates alerts in a local state file, and can run once or as a loop.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


API_ROOT = "https://api.dexscreener.com"
WATCH_CHAINS = {"ethereum", "solana", "bsc", "base"}
STATE_VERSION = 1

NARRATIVES = [
    ("AI", ["ai", "agent", "gpt", "llm", "bot", "compute", "model"]),
    ("Meme", ["meme", "dog", "cat", "pepe", "frog", "viral", "mascot"]),
    ("DeFi", ["defi", "yield", "swap", "vault", "stake", "liquidity", "lend"]),
    ("Gaming", ["game", "gaming", "play", "arena", "quest", "metaverse"]),
    ("RWA", ["rwa", "real world", "asset", "treasury", "bond", "credit"]),
    ("DePIN", ["depin", "infra", "node", "sensor", "storage", "wireless"]),
    ("Social", ["social", "creator", "community", "fan", "chat"]),
    ("Infra", ["layer", "rollup", "oracle", "bridge", "index", "protocol"]),
    ("NFT", ["nft", "collectible", "ordinal", "art"]),
    ("Privacy", ["privacy", "zk", "zero knowledge", "encrypt"]),
]


@dataclass(frozen=True)
class Alert:
    key: str
    symbol: str
    name: str
    chain_id: str
    address: str
    url: str
    source: str
    score: int
    narratives: list[str]
    volume_24h: float
    liquidity: float
    change_24h: float
    boost: float
    age_hours: float | None
    risks: list[str]


def now_ts() -> int:
    return int(time.time())


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def request_json(path: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(
        f"{API_ROOT}{path}",
        headers={
            "Accept": "application/json",
            "User-Agent": "on-chain-narrative-monitor/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    value = json.loads(payload)
    if isinstance(value, list):
        return value
    if not value:
        return []
    return [value]


def chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def get_nested(value: dict[str, Any] | None, *path: str, default: Any = None) -> Any:
    current: Any = value or {}
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def merge_sources() -> list[dict[str, Any]]:
    endpoints = [
        "/token-profiles/latest/v1",
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
        "/community-takeovers/latest/v1",
    ]
    merged: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        for item in request_json(endpoint):
            chain_id = item.get("chainId")
            address = item.get("tokenAddress")
            if chain_id not in WATCH_CHAINS or not address:
                continue
            key = f"{chain_id}:{address}".lower()
            merged[key] = {**merged.get(key, {}), **item}
    return list(merged.values())[:220]


def hydrate_pairs(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_chain: dict[str, list[str]] = {}
    for item in items:
        by_chain.setdefault(item["chainId"], []).append(item["tokenAddress"])

    pairs: dict[str, dict[str, Any]] = {}
    for chain_id, addresses in by_chain.items():
        unique = sorted(set(addresses))
        for batch in chunks(unique, 30):
            path = f"/tokens/v1/{chain_id}/{','.join(batch)}"
            try:
                for pair in request_json(path):
                    base_address = get_nested(pair, "baseToken", "address")
                    if base_address:
                        pairs[f"{chain_id}:{base_address}".lower()] = pair
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                print(f"[warn] pair hydrate failed for {chain_id}: {exc}", file=sys.stderr)
    return pairs


def text_for(item: dict[str, Any], pair: dict[str, Any] | None) -> str:
    links = " ".join(
        f"{link.get('label', '')} {link.get('type', '')} {link.get('url', '')}"
        for link in item.get("links") or []
        if isinstance(link, dict)
    )
    socials = " ".join(
        f"{social.get('platform', '')} {social.get('handle', '')}"
        for social in get_nested(pair, "info", "socials", default=[]) or []
        if isinstance(social, dict)
    )
    parts = [
        item.get("description", ""),
        links,
        socials,
        get_nested(pair, "baseToken", "name", default=""),
        get_nested(pair, "baseToken", "symbol", default=""),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def detect_narratives(item: dict[str, Any], pair: dict[str, Any] | None) -> list[str]:
    haystack = text_for(item, pair)
    hits = [name for name, words in NARRATIVES if any(word in haystack for word in words)]
    return hits or ["Emerging"]


def score_token(item: dict[str, Any], pair: dict[str, Any] | None) -> int:
    volume = float(get_nested(pair, "volume", "h24", default=0) or 0)
    liquidity = float(get_nested(pair, "liquidity", "usd", default=0) or 0)
    change = float(get_nested(pair, "priceChange", "h24", default=0) or 0)
    buys = float(get_nested(pair, "txns", "h24", "buys", default=0) or 0)
    sells = float(get_nested(pair, "txns", "h24", "sells", default=0) or 0)
    boost = float(item.get("amount") or item.get("totalAmount") or get_nested(pair, "boosts", "active", default=0) or 0)
    created = float(pair.get("pairCreatedAt") or 0) if pair else 0
    age_hours = max(1.0, (time.time() * 1000 - created) / 3_600_000) if created else 720.0
    buy_pressure = buys / (buys + sells) if buys + sells > 0 else 0.5

    score = 18.0
    score += min(25.0, math.log10(volume + 1) * 3.2)
    score += min(18.0, math.log10(liquidity + 1) * 2.2)
    score += max(-16.0, min(22.0, change / 4.0))
    score += min(12.0, boost * 1.8)
    score += max(-6.0, min(10.0, (buy_pressure - 0.5) * 28.0))
    score += 8.0 if age_hours < 48 else 4.0 if age_hours < 168 else 0.0
    return round(max(0.0, min(100.0, score)))


def age_hours(pair: dict[str, Any] | None) -> float | None:
    created = float(pair.get("pairCreatedAt") or 0) if pair else 0
    if not created:
        return None
    return max(1.0, (time.time() * 1000 - created) / 3_600_000)


def risk_notes(pair: dict[str, Any] | None, score: int, liquidity: float, change: float, boost: float) -> list[str]:
    notes: list[str] = []
    if not pair:
        notes.append("pair data sparse")
    if liquidity and liquidity < 50_000:
        notes.append("thin liquidity")
    if change > 80:
        notes.append("extended 24h move")
    if boost > 0:
        notes.append("paid boost present")
    if score >= 75 and not notes:
        notes.append("high momentum")
    return notes or ["clean first pass"]


def build_alerts(
    items: list[dict[str, Any]],
    pairs: dict[str, dict[str, Any]],
    min_score: int,
    min_liquidity: float,
    min_volume: float,
) -> list[Alert]:
    alerts: list[Alert] = []
    for item in items:
        key = f"{item['chainId']}:{item['tokenAddress']}".lower()
        pair = pairs.get(key)
        score = score_token(item, pair)
        volume = float(get_nested(pair, "volume", "h24", default=0) or 0)
        liquidity = float(get_nested(pair, "liquidity", "usd", default=0) or 0)
        if score < min_score or liquidity < min_liquidity or volume < min_volume:
            continue
        change = float(get_nested(pair, "priceChange", "h24", default=0) or 0)
        boost = float(item.get("amount") or item.get("totalAmount") or get_nested(pair, "boosts", "active", default=0) or 0)
        alerts.append(
            Alert(
                key=key,
                symbol=str(get_nested(pair, "baseToken", "symbol", default=item["tokenAddress"][:6]) or item["tokenAddress"][:6]),
                name=str(get_nested(pair, "baseToken", "name", default="Unlisted token") or "Unlisted token"),
                chain_id=item["chainId"],
                address=item["tokenAddress"],
                url=str(pair.get("url") if pair else item.get("url", "")),
                source="boost" if boost else "takeover" if item.get("claimDate") else "profile",
                score=score,
                narratives=detect_narratives(item, pair),
                volume_24h=volume,
                liquidity=liquidity,
                change_24h=change,
                boost=boost,
                age_hours=age_hours(pair),
                risks=risk_notes(pair, score, liquidity, change, boost),
            )
        )
    return sorted(alerts, key=lambda alert: alert.score, reverse=True)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "seen": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": STATE_VERSION, "seen": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def prune_seen(state: dict[str, Any], ttl_hours: float) -> None:
    cutoff = now_ts() - int(ttl_hours * 3600)
    seen = state.setdefault("seen", {})
    for key in list(seen):
        if int(seen[key].get("last_alerted_at", 0)) < cutoff:
            del seen[key]


def unseen_alerts(alerts: list[Alert], state: dict[str, Any], max_alerts: int) -> list[Alert]:
    seen = state.setdefault("seen", {})
    fresh = [alert for alert in alerts if alert.key not in seen]
    return fresh[:max_alerts]


def mark_seen(alerts: list[Alert], state: dict[str, Any]) -> None:
    seen = state.setdefault("seen", {})
    stamp = now_ts()
    for alert in alerts:
        seen[alert.key] = {
            "last_alerted_at": stamp,
            "score": alert.score,
            "symbol": alert.symbol,
            "chain_id": alert.chain_id,
            "address": alert.address,
        }


def compact_money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.0f}"


def age_label(hours: float | None) -> str:
    if hours is None:
        return "--"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f}d"


def format_alert(alert: Alert) -> str:
    title = f"{html.escape(alert.symbol)} / {html.escape(alert.name)}"
    url = html.escape(alert.url or f"https://dexscreener.com/{alert.chain_id}/{alert.address}")
    lines = [
        f"<b>On-chain narrative alert</b>",
        f"<a href=\"{url}\">{title}</a>",
        "",
        f"Score: <b>{alert.score}</b>",
        f"Chain: {html.escape(alert.chain_id.upper())}",
        f"Narrative: {html.escape(', '.join(alert.narratives))}",
        f"Source: {html.escape(alert.source)}",
        f"24h: {alert.change_24h:+.1f}%",
        f"Volume: {compact_money(alert.volume_24h)}",
        f"Liquidity: {compact_money(alert.liquidity)}",
        f"Age: {age_label(alert.age_hours)}",
        f"Risk: {html.escape(', '.join(alert.risks))}",
        "",
        f"<code>{html.escape(alert.address)}</code>",
        utc_stamp(),
    ]
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str, timeout: int = 20) -> None:
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"User-Agent": "on-chain-narrative-monitor/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {exc.code}: {body}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram returned not ok: {payload}")


def run_once(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file)
    state = load_state(state_path)
    if args.reset_state:
        state = {"version": STATE_VERSION, "seen": {}}
    prune_seen(state, args.seen_ttl_hours)

    items = merge_sources()
    pairs = hydrate_pairs(items)
    alerts = build_alerts(items, pairs, args.min_score, args.min_liquidity, args.min_volume)
    fresh = unseen_alerts(alerts, state, args.max_alerts)

    print(f"[{utc_stamp()}] scanned={len(items)} candidates={len(alerts)} fresh={len(fresh)}")
    if not fresh:
        save_state(state_path, state)
        return 0

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not args.dry_run and (not token or not chat_id):
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or run with --dry-run.")

    for alert in fresh:
        message = format_alert(alert)
        if args.dry_run:
            print("\n--- DRY RUN TELEGRAM MESSAGE ---")
            print(message)
        else:
            send_telegram(token, chat_id, message)
            print(f"[sent] {alert.chain_id}:{alert.symbol} score={alert.score}")

    if args.dry_run:
        print("[dry-run] state not updated")
    else:
        mark_seen(fresh, state)
        save_state(state_path, state)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the on-chain narrative monitor.")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    parser.add_argument("--loop", action="store_true", help="Keep scanning forever.")
    parser.add_argument("--dry-run", action="store_true", help="Print Telegram messages without sending.")
    parser.add_argument("--interval-minutes", type=float, default=10.0, help="Loop interval in minutes.")
    parser.add_argument("--min-score", type=int, default=55, help="Minimum alert score.")
    parser.add_argument("--min-liquidity", type=float, default=25_000, help="Minimum USD liquidity.")
    parser.add_argument("--min-volume", type=float, default=50_000, help="Minimum 24h USD volume.")
    parser.add_argument("--max-alerts", type=int, default=5, help="Max fresh alerts per scan.")
    parser.add_argument("--seen-ttl-hours", type=float, default=24.0, help="Suppress repeat alerts for this many hours.")
    parser.add_argument("--state-file", default=".monitor_state.json", help="Path to dedupe state JSON.")
    parser.add_argument("--reset-state", action="store_true", help="Clear dedupe state before scanning.")
    args = parser.parse_args(argv)
    if not args.once and not args.loop:
        args.once = True
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
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
