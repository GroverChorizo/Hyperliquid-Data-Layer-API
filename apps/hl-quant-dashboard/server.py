#!/usr/bin/env python3
"""HL Quant Dashboard — local-first server.

Run:
    cd apps/hl-quant-dashboard
    python server.py            # serves http://127.0.0.1:8787

Design:
  * Pure standard library (http.server + json). No web framework, no CDN.
  * The browser only ever talks to THIS server. The MoonDev API key stays
    server-side (loaded from .env via the repo's existing client).
  * REAL DATA ONLY. Every data path either returns real upstream JSON or an
    explicit error envelope. There is no synthetic fallback anywhere.

Endpoints:
  GET  /                         -> dashboard
  GET  /static/<file>            -> dashboard assets
  GET  /api/config               -> {key_present, vault_path, strategies}
  GET  /api/data?fn=...&...      -> proxied MoonDev read endpoint (whitelisted)
  POST /api/backtest/run         -> run a backtest on real candles
  POST /api/backtest/export      -> write a markdown report into the vault
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = APP_DIR / "dashboard"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import moondev_client as mdc                       # noqa: E402
from backtest import engine as bt_engine           # noqa: E402
from backtest import strategies as bt_strategies    # noqa: E402
from backtest import vault_export                   # noqa: E402

HOST = os.environ.get("HLQD_HOST", "127.0.0.1")
PORT = int(os.environ.get("HLQD_PORT", "8787"))

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "HLQuantDashboard/0.1"

    # ---- helpers ---------------------------------------------------------
    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        ctype = _STATIC_TYPES.get(path.suffix, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt, *args):  # quieter, single-line logs
        sys.stderr.write("[hlqd] " + (fmt % args) + "\n")

    # ---- routing ---------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        if route in ("/", "/index.html"):
            self._send_file(DASHBOARD_DIR / "index.html")
            return
        if route.startswith("/static/"):
            name = route[len("/static/"):]
            safe = (DASHBOARD_DIR / name).resolve()
            if DASHBOARD_DIR.resolve() in safe.parents:
                self._send_file(safe)
            else:
                self._send_json({"ok": False, "error": "forbidden"}, 403)
            return
        if route == "/api/config":
            self._send_json({
                "ok": True,
                "key_present": mdc.key_present(),
                "vault_path": str(vault_export.resolve_vault()),
                "strategies": {
                    name: {"defaults": spec["defaults"],
                           "schema": spec["schema"], "doc": spec["doc"]}
                    for name, spec in bt_strategies.REGISTRY.items()
                },
                "intervals": sorted(bt_engine.BARS_PER_YEAR.keys()),
            })
            return
        if route == "/api/data":
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            fn = q.pop("fn", "")
            env = mdc.call(fn, q)
            self._send_json(env, 200 if env.get("ok") else 200)
            return
        self._send_json({"ok": False, "error": f"no route {route}"}, 404)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/backtest/run":
            self._handle_run()
            return
        if route == "/api/backtest/export":
            self._handle_export()
            return
        self._send_json({"ok": False, "error": f"no route {route}"}, 404)

    # ---- backtest handlers ----------------------------------------------
    def _handle_run(self) -> None:
        body = self._read_json_body()
        coin = str(body.get("coin", "BTC")).upper()
        interval = str(body.get("interval", "1h"))
        strategy = str(body.get("strategy", "sma_cross"))
        params = body.get("params") or {}
        try:
            fee_bps = float(body.get("fee_bps", 4.5))
            slippage_bps = float(body.get("slippage_bps", 2.0))
            cost_mult = float(body.get("cost_mult", 1.0))
            oos_frac = float(body.get("oos_frac", 0.3))
        except (TypeError, ValueError):
            self._send_json({"ok": False, "error": "numeric fields invalid"}, 400)
            return

        # 1) fetch REAL candles via the server-side MoonDev client
        env = mdc.call("candles", {"coin": coin, "interval": interval})
        if not env.get("ok"):
            self._send_json({"ok": False, "stage": "fetch", **env}, 200)
            return
        candles = env.get("data")
        if not isinstance(candles, list) or not candles:
            self._send_json({"ok": False, "stage": "fetch",
                             "error": "upstream returned no candles (real data "
                                      "unavailable); not substituting anything."},
                            200)
            return

        # 2) run the backtest on real bars
        try:
            bars = bt_engine.bars_from_moondev_candles(candles)
            signal_fn, resolved = bt_strategies.build_signal(strategy, params)
            result = bt_engine.run_backtest(
                bars, signal_fn, symbol=coin, interval=interval,
                strategy=strategy, params=resolved, fee_bps=fee_bps,
                slippage_bps=slippage_bps, cost_mult=cost_mult, oos_frac=oos_frac)
        except (bt_engine.BacktestError, ValueError) as e:
            self._send_json({"ok": False, "stage": "run", "error": str(e)}, 200)
            return
        self._send_json({"ok": True, "result": result.to_dict()})

    def _handle_export(self) -> None:
        body = self._read_json_body()
        result_d = body.get("result")
        notes = str(body.get("notes", ""))
        vault_path = body.get("vault_path") or None
        if not isinstance(result_d, dict):
            self._send_json({"ok": False, "error": "missing result payload"}, 400)
            return
        try:
            result = _result_from_dict(result_d)
            path = vault_export.export(result, notes=notes, vault_path=vault_path)
        except Exception as e:
            self._send_json({"ok": False, "error": f"export failed: {e}"}, 200)
            return
        self._send_json({"ok": True, "path": str(path)})


def _result_from_dict(d: dict) -> bt_engine.BacktestResult:
    """Rebuild a BacktestResult from the JSON the browser round-trips back.

    We only trust scalar/label fields and the curve for rendering; the report
    is a faithful echo of the run the server already computed.
    """
    fills = [bt_engine.TradeFill(t=f["t"], price=f["price"], from_pos=f["from_pos"],
                                 to_pos=f["to_pos"], cost=f["cost"])
             for f in d.get("fills", [])]
    return bt_engine.BacktestResult(
        symbol=str(d.get("symbol", "?")), interval=str(d.get("interval", "?")),
        strategy=str(d.get("strategy", "?")), params=d.get("params", {}) or {},
        n_bars=int(d.get("n_bars", 0)), start_ms=int(d.get("start_ms", 0)),
        end_ms=int(d.get("end_ms", 0)),
        equity_curve=[float(x) for x in d.get("equity_curve", [1.0])] or [1.0],
        times=[int(x) for x in d.get("times", [])], fills=fills,
        metrics_is=d.get("metrics_is", {}) or {},
        metrics_oos=d.get("metrics_oos", {}) or {},
        fee_bps=float(d.get("fee_bps", 0)), slippage_bps=float(d.get("slippage_bps", 0)),
        cost_mult=float(d.get("cost_mult", 1)), oos_frac=float(d.get("oos_frac", 0)),
        funding_modeled=bool(d.get("funding_modeled", False)),
        status=str(d.get("status", "untested")))


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    key = "set" if mdc.key_present() else "MISSING (real data will be unavailable)"
    sys.stderr.write(
        f"\nHL Quant Dashboard → http://{HOST}:{PORT}\n"
        f"  MOONDEV_API_KEY: {key}\n"
        f"  vault: {vault_export.resolve_vault()}\n"
        f"  (Ctrl-C to stop)\n\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nshutting down.\n")
        httpd.shutdown()


if __name__ == "__main__":
    main()
