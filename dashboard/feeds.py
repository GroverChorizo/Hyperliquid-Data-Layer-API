"""Feed registry mapping the example scripts to live, on-demand API calls.

Each example in ``examples/`` demonstrates one or more endpoints. Here we expose
those same endpoints as named feeds the dashboard fetches via ``/api/feed/{id}``
and renders into themed grids. Feeds are grouped so related examples land on the
same grid (Liquidations, HLP, Order Flow, Positions, Smart Money, Market/Chain).

Every feed pulls real data straight from ``MoonDevAPI``; nothing is synthesised.
"""
from __future__ import annotations

import os
from typing import Any, Callable

# A default Hyperliquid address for the user-scoped feeds (positions/fills).
# Override with GROVER_ADDRESS in .env. This is the HLP_LONG vault used in the
# api.py test-suite — a public, always-active address, handy for a live demo.
DEFAULT_ADDRESS = "0x010461c14e146ac35fe42271bdc1134ee31c703a"


def _addr(params: dict[str, str]) -> str:
    return params.get("address") or os.getenv("GROVER_ADDRESS") or DEFAULT_ADDRESS


def _tf(params: dict[str, str], default: str = "1h") -> str:
    return params.get("tf") or default


# id -> (group, title, source_example, fn(api, params) -> json)
FEEDS: dict[str, tuple[str, str, str, Callable[[Any, dict[str, str]], Any]]] = {
    # ---------- Liquidations ----------
    "liq_stats": ("liquidations", "Aggregate Liquidation Stats", "01", lambda a, p: a.get_liquidation_stats()),
    "liq_recent": ("liquidations", "Hyperliquid Liquidations", "01", lambda a, p: a.get_liquidations(_tf(p))),
    "liq_multi": ("liquidations", "All Exchanges (Combined)", "14", lambda a, p: a.get_all_liquidation_stats()),
    "liq_binance": ("liquidations", "Binance Futures", "13", lambda a, p: a.get_binance_liquidations(_tf(p))),
    "liq_bybit": ("liquidations", "Bybit", "14", lambda a, p: a.get_bybit_liquidations(_tf(p))),
    "liq_okx": ("liquidations", "OKX", "14", lambda a, p: a.get_okx_liquidations(_tf(p))),
    "liq_hip3": ("liquidations", "HIP3 / TradFi Liquidations", "20", lambda a, p: a.get_hip3_liquidation_stats()),
    # ---------- HLP ----------
    "hlp_positions": ("hlp", "HLP Positions", "12", lambda a, p: a.get_hlp_positions()),
    "hlp_sentiment": ("hlp", "HLP Sentiment (Z-Score)", "17", lambda a, p: a.get_hlp_sentiment()),
    "hlp_delta": ("hlp", "HLP Net Delta", "17", lambda a, p: a.get_hlp_delta()),
    "hlp_flips": ("hlp", "HLP Flip History", "17", lambda a, p: a.get_hlp_flips()),
    "hlp_deltas": ("hlp", "HLP Delta (24h)", "12", lambda a, p: a.get_hlp_deltas()),
    "hlp_trade_stats": ("hlp", "HLP Trade Stats", "12", lambda a, p: a.get_hlp_trade_stats()),
    "hlp_timing": ("hlp", "HLP Timing", "18", lambda a, p: a.get_hlp_timing()),
    "hlp_correlation": ("hlp", "HLP Delta/Price Correlation", "18", lambda a, p: a.get_hlp_correlation()),
    "hlp_liquidator_status": ("hlp", "HLP Liquidator Status", "18", lambda a, p: a.get_hlp_liquidator_status()),
    # ---------- Order Flow & Trades ----------
    "orderflow": ("flow", "Order Flow Imbalance", "07", lambda a, p: a.get_orderflow()),
    "orderflow_stats": ("flow", "Order Flow Service", "07", lambda a, p: a.get_orderflow_stats()),
    "imbalance": ("flow", "Buy/Sell Imbalance", "07", lambda a, p: a.get_imbalance(_tf(p))),
    "trades": ("flow", "Recent Trades", "08", lambda a, p: a.get_trades()),
    "large_trades": ("flow", "Large Trades (>$100k)", "08", lambda a, p: a.get_large_trades()),
    "ticks_latest": ("flow", "Latest Tick Prices", "06", lambda a, p: a.get_tick_latest()),
    "buyers": ("flow", "Recent Buyers ($5k+)", "15", lambda a, p: a.get_buyers()),
    # ---------- Positions & Whales ----------
    "positions": ("positions", "Large Positions (near liq)", "02", lambda a, p: a.get_positions()),
    "whales": ("positions", "Whale Trades ($25k+)", "03", lambda a, p: a.get_whales()),
    "user_positions": ("positions", "Tracked Wallet Positions", "10", lambda a, p: a.get_user_positions(_addr(p))),
    "user_fills": ("positions", "Tracked Wallet Fills", "11", lambda a, p: a.get_user_fills(_addr(p), 50)),
    "depositors": ("positions", "Depositors", "16", lambda a, p: a.get_depositors()),
    # ---------- Smart Money ----------
    "sm_leaderboard": ("smart", "Smart Money Leaderboard", "09", lambda a, p: a.get_smart_money_leaderboard()),
    "sm_rankings": ("smart", "Smart vs Dumb Rankings", "09", lambda a, p: a.get_smart_money_rankings()),
    "sm_signals": ("smart", "Smart Money Signals", "09", lambda a, p: a.get_smart_money_signals(_tf(p))),
    # ---------- Market & Chain ----------
    "prices": ("market", "Prices / Funding / OI", "19", lambda a, p: a.get_prices()),
    "events": ("market", "Blockchain Events", "04", lambda a, p: a.get_events()),
    "contracts": ("market", "Contract Registry", "05", lambda a, p: a.get_contracts()),
    "hip3_meta": ("market", "HIP3 Market Data", "21", lambda a, p: a.get_hip3_meta()),
    "hip3_ticks_stats": ("market", "HIP3 Tick Collector", "22", lambda a, p: a.get_hip3_tick_stats()),
}


def run_feed(api: Any, feed_id: str, params: dict[str, str]) -> dict[str, Any]:
    """Execute a feed and wrap the result with metadata for the client."""
    entry = FEEDS.get(feed_id)
    if entry is None:
        return {"ok": False, "error": f"unknown feed: {feed_id}"}
    group, title, example, fn = entry
    try:
        data = fn(api, params)
        return {"ok": True, "id": feed_id, "group": group, "title": title, "example": example, "data": data}
    except Exception as exc:  # noqa: BLE001 - surface the error to the panel
        return {"ok": False, "id": feed_id, "title": title, "example": example, "error": f"{type(exc).__name__}: {exc}"}
