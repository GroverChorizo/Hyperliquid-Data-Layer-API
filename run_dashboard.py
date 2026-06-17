#!/usr/bin/env python3
"""Launch the Grover streaming dashboard.

    python run_dashboard.py            # http://127.0.0.1:8787
    GROVER_SYMBOLS="BTC ETH SOL HYPE FARTCOIN" python run_dashboard.py

Environment:
    MOONDEV_API_KEY   required for live data (set in .env)
    GROVER_SYMBOLS    space/comma separated watchlist (default: BTC ETH SOL HYPE)
    GROVER_INTERVAL   candle interval: 1m 5m 15m 1h 4h 1d (default: 5m)
    GROVER_POLL_SEC   poll cadence in seconds (default: 30, API updates every 30s)
    GROVER_DATA_DIR   data-lake output dir (default: ./data)
    GROVER_HOST / GROVER_PORT   bind address (default: 127.0.0.1:8787)
"""
from __future__ import annotations

import os
import webbrowser

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    host = os.getenv("GROVER_HOST", "127.0.0.1")
    port = int(os.getenv("GROVER_PORT", "8787"))
    if os.getenv("GROVER_OPEN", "1") == "1":
        with __import__("contextlib").suppress(Exception):
            webbrowser.open(f"http://{host}:{port}")
    uvicorn.run("dashboard.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
