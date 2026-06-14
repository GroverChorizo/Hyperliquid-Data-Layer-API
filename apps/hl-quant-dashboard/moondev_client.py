"""Server-side wrapper around the repo's existing MoonDevAPI client.

Why this exists:
  * The API key stays on the server. The browser never sees MOONDEV_API_KEY;
    it only talks to this app's /api/* proxy.
  * REAL DATA ONLY. If the key is missing or the upstream call fails, we return
    an explicit error envelope — we NEVER fabricate a fallback payload.
  * Only a whitelisted set of read-only MoonDevAPI methods is callable from the
    browser, with their arguments validated here.

Returns envelopes shaped like:
  {"ok": True,  "fn": ..., "data": <real upstream json>}
  {"ok": False, "fn": ..., "error": "...", "status": <http status or None>}
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Make the repo-root api.py importable (apps/hl-quant-dashboard/ -> repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # pragma: no cover - import shim
    from api import MoonDevAPI  # type: ignore
except Exception as _e:  # pragma: no cover
    MoonDevAPI = None  # type: ignore
    _IMPORT_ERR = _e
else:
    _IMPORT_ERR = None

try:
    import requests  # noqa: F401
    from requests import HTTPError, RequestException
except Exception:  # pragma: no cover
    HTTPError = RequestException = Exception  # type: ignore


# Whitelisted, read-only endpoints. name -> (method, arg_builder).
# arg_builder maps a validated query dict to positional/keyword call args.
def _validate_interval(v: Optional[str]) -> str:
    allowed = {"1m", "5m", "15m", "1h", "4h", "1d"}
    v = (v or "1h")
    if v not in allowed:
        raise ValueError(f"interval must be one of {sorted(allowed)}")
    return v


def _validate_liq_tf(v: Optional[str]) -> str:
    allowed = {"10m", "1h", "4h", "12h", "24h", "2d", "7d", "14d", "30d"}
    v = (v or "1h")
    if v not in allowed:
        raise ValueError(f"timeframe must be one of {sorted(allowed)}")
    return v


def _coin(v: Optional[str]) -> str:
    v = (v or "BTC").upper()
    if not v.isalnum() or len(v) > 16:
        raise ValueError("coin must be alphanumeric, <= 16 chars")
    return v


WHITELIST: Dict[str, Callable[["MoonDevAPI", Dict[str, Any]], Any]] = {
    "health": lambda c, q: c.health(),
    "prices": lambda c, q: c.get_prices(),
    "candle_symbols": lambda c, q: c.get_candle_symbols(),
    "candles": lambda c, q: c.get_candles(_coin(q.get("coin")),
                                          interval=_validate_interval(q.get("interval"))),
    "liquidations": lambda c, q: c.get_liquidations(_validate_liq_tf(q.get("timeframe"))),
    "all_liquidations": lambda c, q: c.get_all_liquidations(_validate_liq_tf(q.get("timeframe"))),
    "whales": lambda c, q: c.get_whales(),
    "positions": lambda c, q: c.get_positions(),
    "orderflow": lambda c, q: c.get_orderflow(),
    "hlp_positions": lambda c, q: c.get_hlp_positions(),
    "hlp_sentiment": lambda c, q: c.get_hlp_sentiment(),
    "smart_money_rankings": lambda c, q: c.get_smart_money_rankings(),
    "smart_money_leaderboard": lambda c, q: c.get_smart_money_leaderboard(),
    "smart_money_signals": lambda c, q: c.get_smart_money_signals(_validate_liq_tf(q.get("timeframe"))),
}

# Endpoints safe to call without an API key (upstream allows no-auth).
NO_AUTH = {"health"}


class MoonDevError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


_client: Optional["MoonDevAPI"] = None


def get_client() -> "MoonDevAPI":
    global _client
    if MoonDevAPI is None:
        raise MoonDevError(f"MoonDevAPI client unavailable: {_IMPORT_ERR}")
    if _client is None:
        _client = MoonDevAPI()
    return _client


def key_present() -> bool:
    try:
        return bool(get_client().api_key)
    except MoonDevError:
        return False


def call(fn: str, query: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a whitelisted endpoint. Returns an honest envelope, never fakes."""
    if fn not in WHITELIST:
        return {"ok": False, "fn": fn, "error": f"unknown endpoint {fn!r}",
                "status": 404}
    try:
        client = get_client()
    except MoonDevError as e:
        return {"ok": False, "fn": fn, "error": str(e), "status": None}

    if fn not in NO_AUTH and not client.api_key:
        return {"ok": False, "fn": fn,
                "error": "MOONDEV_API_KEY not set — real data unavailable. "
                         "Add it to .env (never commit it). No synthetic "
                         "fallback is provided by design.",
                "status": 401}
    try:
        data = WHITELIST[fn](client, query)
        return {"ok": True, "fn": fn, "data": data}
    except ValueError as e:  # bad arguments
        return {"ok": False, "fn": fn, "error": f"bad request: {e}", "status": 400}
    except HTTPError as e:  # upstream HTTP error (e.g. 401/403 bad key)
        status = getattr(getattr(e, "response", None), "status_code", None)
        return {"ok": False, "fn": fn,
                "error": f"upstream HTTP error: {e}", "status": status}
    except RequestException as e:  # network/timeout
        return {"ok": False, "fn": fn,
                "error": f"upstream unreachable: {e}", "status": None}
    except Exception as e:  # last-resort, still honest
        return {"ok": False, "fn": fn, "error": f"unexpected: {e}", "status": 500}
