"""Streaming recorder for the Grover dashboard.

Polls the Moon Dev / Hyperliquid Data Layer API on a fixed cadence, persists
every stream to a local data lake under ``./data`` (CSV for numeric series on
the ``ts,open,high,low,close,volume`` contract, JSONL for event-like feeds so the
bots can consume them), and hands each update to the web server as a JSON
message for live streaming to the browser.

Real data only: every value here comes from the API. Nothing is simulated.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import requests

try:  # api.py lives at the project root
    from api import MoonDevAPI
except Exception:  # pragma: no cover - import guard
    MoonDevAPI = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent

# Bar-open epoch-ms width per interval, for deciding when a candle has closed.
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

# How many points of each rolling series to keep in memory for the snapshot a
# new browser client receives on connect (sparklines fill instantly).
SPARK_LEN = 180


def _f(value: Any) -> float | None:
    """Best-effort float; the API returns most numbers as strings."""
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def now_ms() -> int:
    return int(time.time() * 1000)


def _env_symbols() -> list[str]:
    raw = os.getenv("GROVER_SYMBOLS", "").strip()
    if not raw:
        return ["BTC", "ETH", "SOL", "HYPE"]
    parts = [p.strip().upper() for p in raw.replace(",", " ").split()]
    return [p for p in parts if p] or ["BTC", "ETH", "SOL", "HYPE"]


class LakeWriter:
    """Append-only writer for the local data lake.

    CSV series carry a header on first write and strictly-increasing ``ts``.
    JSONL feeds are append-only with in-memory de-duplication by key.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._headers: set[Path] = set()
        self._seen: dict[str, set[str]] = {}

    def _ensure(self, path: Path, header: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path not in self._headers and not path.exists():
            with path.open("w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(header)
        self._headers.add(path)

    def append_row(self, rel: str, header: list[str], row: list[Any]) -> None:
        path = self.root / rel
        self._ensure(path, header)
        with path.open("a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(row)

    def append_rows(self, rel: str, header: list[str], rows: list[list[Any]]) -> None:
        if not rows:
            return
        path = self.root / rel
        self._ensure(path, header)
        with path.open("a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)

    def append_jsonl(self, rel: str, records: list[dict[str, Any]], *, key: str) -> int:
        """Append records whose ``key`` field hasn't been written this run."""
        if not records:
            return 0
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        seen = self._seen.setdefault(rel, set())
        fresh = []
        for rec in records:
            ident = str(rec.get(key, ""))
            if ident and ident in seen:
                continue
            if ident:
                seen.add(ident)
            fresh.append(rec)
        if not fresh:
            return 0
        with path.open("a", encoding="utf-8") as fh:
            for rec in fresh:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        if len(seen) > 20_000:  # bound memory; oldest ids drop out of dedup
            self._seen[rel] = set(list(seen)[-10_000:])
        return len(fresh)


class Recorder:
    """Owns the poll loop, the lake writer, and the latest in-memory state."""

    def __init__(
        self,
        *,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.symbols = _env_symbols()
        self.interval = os.getenv("GROVER_INTERVAL", "5m")
        if self.interval not in INTERVAL_MS:
            self.interval = "5m"
        self.poll_sec = max(10, int(os.getenv("GROVER_POLL_SEC", "30")))
        self.data_dir = Path(os.getenv("GROVER_DATA_DIR", str(ROOT / "data")))
        self.lake = LakeWriter(self.data_dir)
        self.on_message = on_message

        self.api = MoonDevAPI() if MoonDevAPI is not None else None
        self.has_key = bool(os.getenv("MOONDEV_API_KEY"))

        # candles[symbol] -> {ts: {t,o,h,l,c,v}}
        self.candles: dict[str, dict[int, dict[str, float]]] = {s: {} for s in self.symbols}
        self._last_closed: dict[str, int] = {s: 0 for s in self.symbols}

        # rolling series for sparklines: deque of [ts, value]
        self.price_hist: dict[str, deque[list[float]]] = {s: deque(maxlen=SPARK_LEN) for s in self.symbols}
        self.oi_hist: dict[str, deque[list[float]]] = {s: deque(maxlen=SPARK_LEN) for s in self.symbols}
        self.funding_hist: dict[str, deque[list[float]]] = {s: deque(maxlen=SPARK_LEN) for s in self.symbols}

        self.panels: dict[str, Any] = {}  # latest liqs / hlp / fng / trades / errors
        self.tape: deque[dict[str, Any]] = deque(maxlen=60)
        self.errors: list[str] = []
        self.updated = 0

    # ----- public API for the server -----
    def config(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "interval": self.interval,
            "poll_sec": self.poll_sec,
            "intervals": list(INTERVAL_MS),
            "has_key": self.has_key,
            "data_dir": str(self.data_dir),
        }

    def snapshot(self) -> dict[str, Any]:
        """Full state for a freshly-connected browser client."""
        return {
            "type": "snapshot",
            "config": self.config(),
            "candles": {
                s: sorted(self.candles[s].values(), key=lambda c: c["t"]) for s in self.symbols
            },
            "spark": {
                s: {
                    "price": list(self.price_hist[s]),
                    "oi": list(self.oi_hist[s]),
                    "funding": list(self.funding_hist[s]),
                }
                for s in self.symbols
            },
            "panels": self.panels,
            "tape": list(self.tape),
            "errors": self.errors,
            "updated": self.updated,
        }

    # ----- the loop -----
    async def run(self) -> None:
        await self._backfill()
        while True:
            start = time.monotonic()
            with contextlib.suppress(Exception):
                await self._poll()
            await asyncio.sleep(max(1.0, self.poll_sec - (time.monotonic() - start)))

    async def _backfill(self) -> None:
        if self.api is None or not self.has_key:
            self.errors = ["MOONDEV_API_KEY not set — dashboard is offline. Add it to .env and restart."]
            return

        def work() -> None:
            for sym in self.symbols:
                with contextlib.suppress(Exception):
                    raw = self.api.get_candles(sym, interval=self.interval)
                    self._ingest_candles(sym, raw, persist=True)

        await asyncio.to_thread(work)
        self.updated = now_ms()

    async def _poll(self) -> None:
        if self.api is None or not self.has_key:
            return
        errors: list[str] = []

        def safe(name: str, fn: Callable[[], Any]) -> Any:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - record, never crash the loop
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                return None

        prices = await asyncio.to_thread(lambda: safe("prices", self.api.get_prices))
        liqs = await asyncio.to_thread(lambda: safe("liquidations", self.api.get_all_liquidation_stats))
        hlp = await asyncio.to_thread(lambda: safe("hlp", self.api.get_hlp_sentiment))
        trades = await asyncio.to_thread(lambda: safe("trades", self.api.get_trades))
        candles: dict[str, Any] = {}
        for sym in self.symbols:
            candles[sym] = await asyncio.to_thread(
                lambda s=sym: safe(f"candles:{s}", lambda: self.api.get_candles(s, interval=self.interval))
            )
        fng = await asyncio.to_thread(lambda: safe("fng", self._fetch_fng))

        ts = now_ms()
        tick: dict[str, Any] = {"type": "tick", "ts": ts, "symbols": {}}

        # --- prices / funding / OI per symbol ---
        if isinstance(prices, dict):
            pmap = prices.get("prices", {})
            fmap = prices.get("funding_rates", {})
            omap = prices.get("open_interest", {})
            for sym in self.symbols:
                price = _f(pmap.get(sym))
                funding = _f(fmap.get(sym))
                oi = _f(omap.get(sym))
                entry: dict[str, Any] = {}
                if price is not None:
                    self.price_hist[sym].append([ts, price])
                    self.lake.append_row(f"prices/{sym}.csv", ["ts", "price"], [ts, price])
                    entry["price"] = price
                if funding is not None:
                    self.funding_hist[sym].append([ts, funding])
                    self.lake.append_row(f"funding/{sym}.csv", ["ts", "funding"], [ts, funding])
                    entry["funding"] = funding
                if oi is not None:
                    self.oi_hist[sym].append([ts, oi])
                    self.lake.append_row(f"oi/{sym}.csv", ["ts", "oi"], [ts, oi])
                    entry["oi"] = oi
                if entry:
                    tick["symbols"][sym] = entry

        # --- candles (persist closed bars, stream the forming bar) ---
        candle_msg: dict[str, dict[str, float]] = {}
        for sym in self.symbols:
            raw = candles.get(sym)
            if raw:
                latest = self._ingest_candles(sym, raw, persist=True)
                if latest is not None:
                    candle_msg[sym] = latest
        if candle_msg:
            tick["candles"] = candle_msg

        # --- liquidations ---
        if isinstance(liqs, dict):
            self._record_liqs(ts, liqs)
            tick["liqs"] = self.panels.get("liqs")

        # --- HLP sentiment (the signature panel) ---
        if isinstance(hlp, dict):
            row = {
                "ts": ts,
                "net_delta": _f(hlp.get("net_delta")),
                "z_score": _f(hlp.get("z_score")),
                "signal": str(hlp.get("signal", "")),
                "percentile": _f(hlp.get("percentile")),
            }
            self.lake.append_row(
                "hlp_sentiment.csv",
                ["ts", "net_delta", "z_score", "signal", "percentile"],
                [row["ts"], row["net_delta"], row["z_score"], row["signal"], row["percentile"]],
            )
            self.panels["hlp"] = row
            tick["hlp"] = row

        # --- fear & greed ---
        if isinstance(fng, list) and fng:
            self.panels["fng"] = fng
            latest = fng[0]
            self.lake.append_row(
                "fng.csv",
                ["ts", "value", "classification"],
                [ts, latest.get("value"), latest.get("value_classification")],
            )
            tick["fng"] = fng

        # --- trade tape ---
        norm = self._normalize_trades(trades)
        if norm:
            self.lake.append_jsonl("trades.jsonl", norm, key="id")
            for t in norm[:20]:
                self.tape.appendleft(t)
            tick["tape"] = norm[:20]

        self.errors = errors
        if errors:
            tick["errors"] = errors
        self.updated = ts

        if self.on_message is not None:
            await self.on_message(tick)

    # ----- helpers -----
    def _ingest_candles(self, sym: str, raw: Any, *, persist: bool) -> dict[str, float] | None:
        """Merge API candles into memory; persist newly-closed bars. Returns latest bar."""
        if not isinstance(raw, list) or not raw:
            return None
        store = self.candles[sym]
        latest: dict[str, float] | None = None
        for c in raw:
            t = _f(c.get("t"))
            o, h, low, close = _f(c.get("o")), _f(c.get("h")), _f(c.get("l")), _f(c.get("c"))
            if t is None or close is None:
                continue
            bar = {
                "t": int(t),
                "o": o or close,
                "h": h or close,
                "l": low or close,
                "c": close,
                "v": _f(c.get("v")) or 0.0,
            }
            store[bar["t"]] = bar
            latest = bar
        # bound memory to a sensible window
        if len(store) > 1500:
            for k in sorted(store)[:-1500]:
                del store[k]
        if persist:
            self._persist_closed(sym)
        return latest

    def _persist_closed(self, sym: str) -> None:
        width = INTERVAL_MS[self.interval]
        cutoff = now_ms() - width  # a bar is closed once its open is a full width in the past
        store = self.candles[sym]
        last = self._last_closed[sym]
        rows: list[list[Any]] = []
        for t in sorted(store):
            if t <= last or t > cutoff:
                continue
            b = store[t]
            rows.append([b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]])
            self._last_closed[sym] = t
        self.lake.append_rows(
            f"candles/{sym}-{self.interval}.csv",
            ["ts", "open", "high", "low", "close", "volume"],
            rows,
        )

    def _record_liqs(self, ts: int, liqs: dict[str, Any]) -> None:
        total_count = liqs.get("total_count", liqs.get("count"))
        total_volume = _f(liqs.get("total_volume") or liqs.get("total_value_usd"))
        by_exchange = liqs.get("by_exchange") or {}
        by_side = liqs.get("by_side") or {}
        exch: dict[str, float] = {}
        rows: list[list[Any]] = []
        if isinstance(by_exchange, dict):
            for ex, info in by_exchange.items():
                vol = _f(info.get("volume")) if isinstance(info, dict) else _f(info)
                cnt = info.get("count") if isinstance(info, dict) else None
                if vol is not None:
                    exch[str(ex)] = vol
                    rows.append([ts, ex, cnt, vol])
        rows.append([ts, "TOTAL", total_count, total_volume])
        self.lake.append_rows("liquidations.csv", ["ts", "exchange", "count", "volume"], rows)
        longs = _f(by_side.get("long")) if isinstance(by_side, dict) else None
        shorts = _f(by_side.get("short")) if isinstance(by_side, dict) else None
        self.panels["liqs"] = {
            "ts": ts,
            "total_count": total_count,
            "total_volume": total_volume,
            "by_exchange": exch,
            "long": longs,
            "short": shorts,
        }

    def _normalize_trades(self, trades: Any) -> list[dict[str, Any]]:
        rows = trades.get("trades") if isinstance(trades, dict) else trades
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for t in rows:
            if not isinstance(t, dict):
                continue
            coin = t.get("coin") or t.get("symbol") or "?"
            side = str(t.get("side") or t.get("dir") or "").upper()
            buy = side.startswith("B") or "LONG" in side or "BUY" in side
            px = _f(t.get("px") or t.get("price"))
            sz = _f(t.get("sz") or t.get("size"))
            usd = _f(t.get("value") or t.get("usd_value"))
            if usd is None and px is not None and sz is not None:
                usd = px * sz
            tid = t.get("tid") or t.get("hash") or f"{t.get('time')}-{coin}-{px}-{sz}"
            out.append(
                {
                    "id": str(tid),
                    "time": t.get("time") or now_ms(),
                    "coin": coin,
                    "side": "buy" if buy else "sell",
                    "px": px,
                    "sz": sz,
                    "usd": usd,
                }
            )
        return out

    def _fetch_fng(self) -> list[dict[str, Any]]:
        resp = requests.get(
            "https://api.alternative.me/fng/",
            params={"limit": 14, "format": "json"},
            headers={"User-Agent": "GroverDashboard/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
