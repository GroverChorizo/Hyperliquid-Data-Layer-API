"""Transparent example strategies. Each is a pure function over real bars.

Contract: signal_fn(bars) -> list[float] targets in [-1, 1], one per bar,
where targets[i] uses ONLY bars[:i+1] (information at the close of bar i).
The engine shifts by one bar so fills land at the next open — strategies must
not peek ahead themselves.

These are intentionally simple and well-known. They are research scaffolding,
NOT an edge. Nothing here is shadow-verified or gauntlet-passed.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from .engine import Bar


def _closes(bars: List[Bar]) -> List[float]:
    return [b.c for b in bars]


def sma_cross(fast: int = 20, slow: int = 50, allow_short: bool = False
              ) -> Callable[[List[Bar]], List[float]]:
    """Long when fast SMA > slow SMA; flat (or short) otherwise."""
    if fast < 1 or slow < 1 or fast >= slow:
        raise ValueError("require 1 <= fast < slow")

    def signal(bars: List[Bar]) -> List[float]:
        c = _closes(bars)
        out: List[float] = []
        fsum = ssum = 0.0
        for i in range(len(c)):
            fsum += c[i]
            ssum += c[i]
            if i >= fast:
                fsum -= c[i - fast]
            if i >= slow:
                ssum -= c[i - slow]
            if i + 1 < slow:                # not enough history yet → flat
                out.append(0.0)
                continue
            fast_ma = fsum / fast
            slow_ma = ssum / slow
            if fast_ma > slow_ma:
                out.append(1.0)
            else:
                out.append(-1.0 if allow_short else 0.0)
        return out

    return signal


def donchian_breakout(lookback: int = 55, exit_lookback: int = 20,
                      allow_short: bool = False
                      ) -> Callable[[List[Bar]], List[float]]:
    """Enter long on a new `lookback`-bar high; exit on `exit_lookback` low.

    Classic turtle-style channel breakout, long-only by default.
    """
    if lookback < 2 or exit_lookback < 1:
        raise ValueError("require lookback >= 2, exit_lookback >= 1")

    def signal(bars: List[Bar]) -> List[float]:
        highs = [b.h for b in bars]
        lows = [b.l for b in bars]
        closes = [b.c for b in bars]
        out: List[float] = []
        pos = 0.0
        for i in range(len(bars)):
            if i < lookback:
                out.append(0.0)
                continue
            # channels formed from PRIOR bars only (exclude current bar i)
            hi = max(highs[i - lookback:i])
            lo = min(lows[i - exit_lookback:i]) if i >= exit_lookback else lows[i - 1]
            entry_hi = max(highs[i - lookback:i])
            entry_lo = min(lows[i - lookback:i])
            price = closes[i]
            if pos <= 0.0 and price > entry_hi:
                pos = 1.0
            elif pos >= 0.0 and allow_short and price < entry_lo:
                pos = -1.0
            elif pos > 0.0 and price < lo:
                pos = 0.0
            elif pos < 0.0 and price > hi:
                pos = 0.0
            out.append(pos)
        return out

    return signal


# registry: name -> (factory, default_params, param_schema)
REGISTRY: Dict[str, dict] = {
    "sma_cross": {
        "factory": sma_cross,
        "defaults": {"fast": 20, "slow": 50, "allow_short": False},
        "schema": {
            "fast": {"type": "int", "min": 1, "max": 400},
            "slow": {"type": "int", "min": 2, "max": 800},
            "allow_short": {"type": "bool"},
        },
        "doc": "Fast/slow simple moving-average crossover.",
    },
    "donchian_breakout": {
        "factory": donchian_breakout,
        "defaults": {"lookback": 55, "exit_lookback": 20, "allow_short": False},
        "schema": {
            "lookback": {"type": "int", "min": 2, "max": 400},
            "exit_lookback": {"type": "int", "min": 1, "max": 400},
            "allow_short": {"type": "bool"},
        },
        "doc": "Channel breakout: enter on N-bar high, exit on M-bar low.",
    },
}


def build_signal(strategy: str, params: dict
                 ) -> Tuple[Callable[[List[Bar]], List[float]], dict]:
    """Return (signal_fn, resolved_params) for the named strategy."""
    if strategy not in REGISTRY:
        raise ValueError(f"unknown strategy {strategy!r}; have {list(REGISTRY)}")
    spec = REGISTRY[strategy]
    merged = dict(spec["defaults"])
    for k, v in (params or {}).items():
        if k not in spec["defaults"]:
            raise ValueError(f"unknown param {k!r} for {strategy}")
        merged[k] = v
    # coerce types from the schema
    for k, meta in spec["schema"].items():
        if meta["type"] == "int":
            merged[k] = int(merged[k])
        elif meta["type"] == "bool":
            merged[k] = bool(merged[k])
    return spec["factory"](**merged), merged
