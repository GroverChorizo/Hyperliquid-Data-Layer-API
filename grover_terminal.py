#!/usr/bin/env python3
"""Grover Market Terminal: terminal-grid + live news/crypto metrics TUI."""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Grid
    from textual.widgets import Footer, Header, Static, TabPane, TabbedContent
except Exception as exc:
    print("Textual is required. Run: pip install -r requirements.txt")
    print(f"Import error: {exc}")
    sys.exit(1)

try:
    from api import MoonDevAPI
except Exception:
    MoonDevAPI = None  # type: ignore[assignment]

load_dotenv()
ROOT = Path(__file__).resolve().parent
RSS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Yahoo Crypto": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD,ETH-USD,SOL-USD,COIN,MSTR&region=US&lang=en-US",
    "CNBC Markets": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}
DEFAULT_COMMANDS = [
    "python examples/01_liquidations.py",
    "python examples/07_orderflow.py",
    "python examples/17_hlp_sentiment.py",
    "python examples/19_market_data.py",
]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def clean(value: Any, limit: int = 120) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def money(value: Any) -> str:
    n = number(value)
    if n is None:
        return "--"
    sign = "-" if n < 0 else ""
    n = abs(n)
    for suffix, scale in [("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)]:
        if n >= scale:
            return f"{sign}${n / scale:.2f}{suffix}"
    return f"{sign}${n:,.2f}"


def pct(value: Any, decimals: int = 2) -> str:
    n = number(value)
    return "--" if n is None else f"{n:.{decimals}f}%"


def get_json(url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> Any:
    session = requests.Session()
    session.headers.update({"User-Agent": "GroverTerminal/0.1"})
    response = session.get(url, headers=headers or {}, params=params or {}, timeout=20)
    response.raise_for_status()
    return response.json()


def get_text(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": "GroverTerminal/0.1"}, timeout=20)
    response.raise_for_status()
    return response.text


def first(node: ET.Element, *names: str) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def parse_rss(source: str, payload: str) -> list[dict[str, str]]:
    root = ET.fromstring(payload.encode("utf-8", errors="ignore"))
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    rows: list[dict[str, str]] = []
    for item in items[:20]:
        title = first(item, "title", "{http://www.w3.org/2005/Atom}title")
        url = first(item, "link", "guid", "{http://www.w3.org/2005/Atom}link")
        if not url:
            link = item.find("{http://www.w3.org/2005/Atom}link")
            url = link.attrib.get("href", "") if link is not None else ""
        if title:
            rows.append({"source": source, "title": clean(title), "url": url})
    return rows


def commands() -> list[str]:
    raw = os.getenv("GROVER_TERMINAL_COMMANDS", "").strip()
    if not raw:
        return DEFAULT_COMMANDS
    with contextlib.suppress(json.JSONDecodeError):
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed][:8]
    return [x.strip() for x in raw.split("||") if x.strip()][:8] or DEFAULT_COMMANDS


class Pane(Static):
    def __init__(self, title: str, border: str = "bright_magenta", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.border = border

    def show(self, body: Any, subtitle: str = "") -> None:
        self.update(Panel(body, title=self.title, subtitle=subtitle, border_style=self.border, padding=(0, 1)))


class CommandPane(Pane):
    def __init__(self, idx: int, command: str) -> None:
        super().__init__(f"TERMINAL {idx}", "cyan", id=f"cmd-{idx}")
        self.command = command
        self.lines: list[str] = []
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.lines = [f"$ {self.command}", "starting…"]
        self.flush("running")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.proc = await asyncio.create_subprocess_shell(
                self.command, cwd=str(ROOT), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            assert self.proc.stdout is not None
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                self.lines.append(ANSI.sub("", line.decode(errors="replace").rstrip()))
                self.lines = self.lines[-120:]
                self.flush("running")
            self.lines.append(f"[exit {await self.proc.wait()}]")
            self.flush("stopped")
        except Exception as exc:
            self.lines.append(f"ERROR: {type(exc).__name__}: {exc}")
            self.flush("error")

    def flush(self, status: str) -> None:
        self.show(Text("\n".join(self.lines[-24:]) or "no output", overflow="fold"), status)

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), timeout=3)


class GroverTerminal(App[None]):
    TITLE = "GROVER MARKET TERMINAL"
    SUB_TITLE = "news • fear/greed • dominance • Hyperliquid • terminal grid"
    BINDINGS = [Binding("r", "refresh_all", "Refresh"), Binding("x", "restart", "Restart terminals"), Binding("q", "quit", "Quit")]
    CSS = """
    Screen { background: #05060a; color: #f4e9ff; }
    Header, Footer { background: #120817; color: #ff79c6; }
    TabPane { padding: 0 1; }
    #market-grid { grid-size: 3 2; grid-gutter: 1 1; height: 1fr; }
    #ops-grid { grid-size: 2 2; grid-gutter: 1 1; height: 1fr; }
    Pane, CommandPane { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.api = MoonDevAPI() if MoonDevAPI is not None else None
        self.cmd_panes: list[CommandPane] = []
        self.state: dict[str, Any] = {"errors": [], "updated": time.time()}
        self.refresh_sec = max(10, int(os.getenv("GROVER_REFRESH_SEC", "30")))
        self.news_sec = max(30, int(os.getenv("GROVER_NEWS_SEC", "90")))

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="market"):
            with TabPane("Live News + Metrics", id="market"):
                with Grid(id="market-grid"):
                    yield Pane("LIVE FINANCIAL NEWS", "bright_magenta", id="news")
                    yield Pane("CRYPTO FEAR & GREED", "red", id="fng")
                    yield Pane("BTC DOMINANCE + GLOBAL CRYPTO", "green", id="global")
                    yield Pane("HYPERLIQUID MARKET DATA", "cyan", id="hl")
                    yield Pane("MULTI-EXCHANGE LIQUIDATIONS", "orange1", id="liq")
                    yield Pane("LIVE CHAIN / API EVENTS", "purple", id="events")
            with TabPane("Terminal Grid", id="ops"):
                with Grid(id="ops-grid"):
                    for idx, cmd in enumerate(commands(), 1):
                        pane = CommandPane(idx, cmd)
                        self.cmd_panes.append(pane)
                        yield pane
        yield Footer()

    async def on_mount(self) -> None:
        self.set_interval(self.refresh_sec, self.action_refresh_all)
        self.set_interval(self.news_sec, self.refresh_news)
        await self.action_refresh_all()
        await self.start_commands()

    async def action_refresh_all(self) -> None:
        await asyncio.gather(self.refresh_news(), self.refresh_public_metrics(), self.refresh_moondev())
        self.render_all()

    async def refresh_news(self) -> None:
        articles: list[dict[str, str]] = []
        errors: list[str] = []
        def work() -> None:
            if os.getenv("NEWSAPI_API_KEY"):
                payload = get_json(
                    "https://newsapi.org/v2/everything",
                    headers={"X-Api-Key": os.getenv("NEWSAPI_API_KEY", "")},
                    params={"q": "bitcoin OR ethereum OR crypto OR hyperliquid OR macro OR markets", "language": "en", "sortBy": "publishedAt", "pageSize": 30},
                )
                for row in payload.get("articles", []):
                    articles.append({"source": clean((row.get("source") or {}).get("name", "NewsAPI")), "title": clean(row.get("title")), "url": row.get("url") or ""})
            for source, url in RSS.items():
                with contextlib.suppress(Exception):
                    articles.extend(parse_rss(source, get_text(url)))
        try:
            await asyncio.to_thread(work)
        except Exception as exc:
            errors.append(f"news: {type(exc).__name__}: {exc}")
        seen: set[str] = set()
        unique = []
        for a in articles:
            key = re.sub(r"\W+", "", a["title"].lower())[:96]
            if key and key not in seen:
                seen.add(key); unique.append(a)
        self.state["articles"] = unique[:40]
        self._replace_errors("news:", errors)
        self.state["updated"] = time.time()
        self.render_all()

    async def refresh_public_metrics(self) -> None:
        errors: list[str] = []
        try:
            fng = await asyncio.to_thread(lambda: get_json("https://api.alternative.me/fng/", params={"limit": 8, "format": "json"}).get("data", []))
            self.state["fng"] = fng
        except Exception as exc:
            errors.append(f"fear_greed: {type(exc).__name__}: {exc}")
        try:
            headers = {"x-cg-demo-api-key": os.getenv("COINGECKO_API_KEY", "")} if os.getenv("COINGECKO_API_KEY") else {}
            glob = await asyncio.to_thread(lambda: get_json("https://api.coingecko.com/api/v3/global", headers=headers).get("data", {}))
            self.state["global"] = glob
        except Exception as exc:
            errors.append(f"global: {type(exc).__name__}: {exc}")
        self._replace_errors("fear_greed:", [e for e in errors if e.startswith("fear_greed:")])
        self._replace_errors("global:", [e for e in errors if e.startswith("global:")])
        self.state["updated"] = time.time()

    async def refresh_moondev(self) -> None:
        errors: list[str] = []
        if self.api is None or not os.getenv("MOONDEV_API_KEY"):
            errors.append("MOONDEV_API_KEY not set; Hyperliquid panels offline.")
            self._replace_errors("MOONDEV", errors)
            return
        def safe(name: str, fn: Any) -> Any:
            try: return fn()
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}"); return {}
        prices = await asyncio.to_thread(lambda: safe("prices", self.api.get_prices))
        liqs = await asyncio.to_thread(lambda: safe("liquidations", self.api.get_all_liquidation_stats))
        hlp = await asyncio.to_thread(lambda: safe("hlp", self.api.get_hlp_sentiment))
        events = await asyncio.to_thread(lambda: safe("events", self.api.get_events))
        self.state.update({"prices": prices or {}, "liqs": liqs or {}, "hlp": hlp or {}, "events_payload": events or []})
        self._replace_errors("MOONDEV", [])
        for prefix in ("prices:", "liquidations:", "hlp:", "events:"):
            self._replace_errors(prefix, [e for e in errors if e.startswith(prefix)])

    def _replace_errors(self, prefix: str, new: list[str]) -> None:
        self.state["errors"] = [e for e in self.state.get("errors", []) if not e.startswith(prefix)] + new

    async def start_commands(self) -> None:
        for pane in self.cmd_panes:
            self.run_worker(pane.start(), exclusive=False, thread=False)

    async def action_restart(self) -> None:
        await asyncio.gather(*(pane.stop() for pane in self.cmd_panes), return_exceptions=True)
        await self.start_commands()

    async def on_unmount(self) -> None:
        await asyncio.gather(*(pane.stop() for pane in self.cmd_panes), return_exceptions=True)

    def render_all(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#news", Pane).show(render_news(self.state), stamp(self.state["updated"]))
            self.query_one("#fng", Pane).show(render_fng(self.state), "source: alternative.me")
            self.query_one("#global", Pane).show(render_global(self.state), "source: coingecko")
            self.query_one("#hl", Pane).show(render_hl(self.state), "source: MoonDev API")
            self.query_one("#liq", Pane).show(render_liqs(self.state), "source: MoonDev API")
            self.query_one("#events", Pane).show(render_events(self.state), error_subtitle(self.state))


def render_news(state: dict[str, Any]) -> Table:
    table = Table.grid(expand=True); table.add_column(justify="right"); table.add_column(ratio=2); table.add_column(ratio=10)
    for i, row in enumerate(state.get("articles", [])[:18], 1):
        table.add_row(f"[bright_black]{i:02d}[/]", f"[green]{clean(row.get('source'), 16)}[/]", clean(row.get("title"), 112))
    if not state.get("articles"):
        table.add_row("--", "offline", "No news received yet. RSS works without keys; NEWSAPI_API_KEY is optional.")
    return table


def render_fng(state: dict[str, Any]) -> Table:
    rows = state.get("fng") or []
    latest = rows[0] if rows else {}
    value = latest.get("value", "--")
    color = "red" if (number(value) or 0) <= 24 else "orange1" if (number(value) or 0) <= 44 else "yellow" if (number(value) or 0) <= 55 else "green"
    grid = Table.grid(expand=True); grid.add_column()
    grid.add_row(Align.center(Text(str(value), style=f"bold {color}")))
    grid.add_row(Align.center(Text(str(latest.get("value_classification", "not connected")).upper(), style=f"bold white on {color}")))
    hist = Table.grid(expand=True); hist.add_column(justify="right"); hist.add_column()
    for row in rows[:7]: hist.add_row(str(row.get("value", "--")), str(row.get("value_classification", "--")))
    grid.add_row(hist)
    return grid


def render_global(state: dict[str, Any]) -> Table:
    data = state.get("global") or {}; dom = data.get("market_cap_percentage", {}); cap = data.get("total_market_cap", {}); vol = data.get("total_volume", {})
    t = Table(expand=True, show_header=False, box=None, padding=(0, 1)); t.add_column(style="bright_black"); t.add_column(style="bold")
    for key, val in [("BTC dominance", pct(dom.get("btc"))), ("ETH dominance", pct(dom.get("eth"))), ("Total market cap", money(cap.get("usd"))), ("24h volume", money(vol.get("usd"))), ("MCap 24h", pct(data.get("market_cap_change_percentage_24h_usd"))), ("Volume 24h", pct(data.get("volume_change_percentage_24h_usd"))), ("Active coins", data.get("active_cryptocurrencies", "--")), ("Markets", data.get("markets", "--"))]:
        t.add_row(key, str(val))
    return t


def render_hl(state: dict[str, Any]) -> Any:
    payload = state.get("prices") or {}; prices = payload.get("prices", {}); funding = payload.get("funding_rates", {}); oi = payload.get("open_interest", {})
    if not prices and not state.get("hlp"): return Text("Not Connected / Configure MOONDEV_API_KEY in .env", style="bold orange1")
    t = Table(expand=True, show_header=True, header_style="bold cyan"); t.add_column("Symbol"); t.add_column("Price", justify="right"); t.add_column("Funding", justify="right"); t.add_column("OI", justify="right")
    for s in ["BTC", "ETH", "SOL", "HYPE"]:
        f = number(funding.get(s)); t.add_row(s, money(prices.get(s)), pct(f * 100 if f is not None else None, 4), money(oi.get(s)))
    hlp = state.get("hlp") or {}
    if hlp: t.add_section(); t.add_row("HLP z-score", str(hlp.get("z_score", "--")), "signal", clean(hlp.get("signal"), 24))
    return t


def render_liqs(state: dict[str, Any]) -> Any:
    data = state.get("liqs") or {}
    if not data: return Text("Not Connected / Configure MOONDEV_API_KEY in .env", style="bold orange1")
    t = Table(expand=True, show_header=False, box=None, padding=(0, 1)); t.add_column(style="bright_black"); t.add_column(style="bold")
    t.add_row("Total count", str(data.get("total_count", "--"))); t.add_row("Total volume", money(data.get("total_volume") or data.get("total_usd") or data.get("total_value")))
    byex = data.get("by_exchange") or data.get("exchanges") or {}
    if isinstance(byex, dict):
        for ex, row in list(byex.items())[:6]: t.add_row(str(ex), money(row.get("volume") if isinstance(row, dict) else row))
    return t


def render_events(state: dict[str, Any]) -> Table:
    payload = state.get("events_payload") or []
    rows = payload.get("events") if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    t = Table.grid(expand=True); t.add_column(ratio=2); t.add_column(ratio=8)
    for row in rows[:14]:
        if isinstance(row, dict): t.add_row(f"[purple]{clean(row.get('type') or row.get('event_type') or 'event', 18)}[/]", clean(row.get("description") or row.get("hash") or json.dumps(row), 105))
        else: t.add_row("event", clean(row, 105))
    if not rows: t.add_row("status", "No event payload yet. MoonDev key may be missing or events endpoint is quiet.")
    if state.get("errors"): t.add_row("warnings", clean(" | ".join(state["errors"][-3:]), 160))
    return t


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("updated %H:%M:%SZ")


def error_subtitle(state: dict[str, Any]) -> str:
    return "connected" if not state.get("errors") else f"{len(state['errors'])} warning(s)"


if __name__ == "__main__":
    GroverTerminal().run()
