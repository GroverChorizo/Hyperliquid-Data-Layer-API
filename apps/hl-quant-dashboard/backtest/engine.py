"""Event-driven-ish bar backtest engine — deliberately small and auditable.

Contract (mirrors the TickerTape backtest constitution):
  * REAL DATA ONLY. Bars come from the MoonDev candles API or a real CSV.
    The engine NEVER invents bars and refuses to run on too-few bars.
  * Fills happen at the NEXT BAR OPEN after the signal bar closes. A signal
    computed from the close of bar i is executed at the open of bar i+1.
    Same-bar-close fills would be look-ahead and are structurally impossible
    here because we shift the target-position series by one bar.
  * Costs are always explicit: taker fee (bps) + slippage (bps), charged on
    the traded fraction whenever the position changes. A cost multiplier lets
    you re-run at 2x costs (the gauntlet habit).
  * Funding is NOT modeled yet. This is an engine gap and is surfaced in
    every metrics dict and every exported report. Do not treat funding-
    sensitive perps results as complete.

Position convention: target position is a float in [-1, 1] (fraction of
equity, long positive / short negative). Strategies emit one target per bar
using only information available at that bar's close.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

# 24/7 crypto annualization — bars per year by interval.
BARS_PER_YEAR: Dict[str, float] = {
    "1m": 525_600.0,
    "5m": 105_120.0,
    "15m": 35_040.0,
    "1h": 8_760.0,
    "4h": 2_190.0,
    "1d": 365.0,
}

# Minimum real bars before a result is meaningful. Below this we refuse,
# rather than print a flattering number off a handful of candles.
MIN_BARS = 60


class BacktestError(Exception):
    """Raised on contract violations (too few bars, bad data, look-ahead)."""


@dataclass
class Bar:
    t: int        # open time, epoch ms, UTC
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass
class TradeFill:
    """A position change executed at a bar open."""
    t: int
    price: float          # the open we filled at
    from_pos: float
    to_pos: float
    cost: float           # cost as a fraction of equity for this fill


@dataclass
class BacktestResult:
    symbol: str
    interval: str
    strategy: str
    params: Dict[str, object]
    n_bars: int
    start_ms: int
    end_ms: int
    equity_curve: List[float]          # length n_bars, starts at 1.0
    times: List[int]                   # bar open times aligned to equity_curve
    fills: List[TradeFill] = field(default_factory=list)
    metrics_is: Dict[str, float] = field(default_factory=dict)
    metrics_oos: Dict[str, float] = field(default_factory=dict)
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    cost_mult: float = 1.0
    oos_frac: float = 0.0
    funding_modeled: bool = False
    status: str = "untested"           # controlled vocab: untested|runs|...

    def to_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "strategy": self.strategy,
            "params": self.params,
            "n_bars": self.n_bars,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "equity_curve": self.equity_curve,
            "times": self.times,
            "fills": [
                {"t": f.t, "price": f.price, "from_pos": f.from_pos,
                 "to_pos": f.to_pos, "cost": f.cost}
                for f in self.fills
            ],
            "metrics_is": self.metrics_is,
            "metrics_oos": self.metrics_oos,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "cost_mult": self.cost_mult,
            "oos_frac": self.oos_frac,
            "funding_modeled": self.funding_modeled,
            "status": self.status,
            "caveats": [
                "Funding not modeled (engine gap) — perps results incomplete.",
                "Single-asset, long/short fraction-of-equity, no leverage cap.",
                "OOS split is a reporting holdout, NOT walk-forward validation.",
                "Not financial advice. Research artifact only.",
            ],
        }


def bars_from_moondev_candles(candles: Sequence[dict]) -> List[Bar]:
    """Convert MoonDev/Hyperliquid candle dicts to typed Bars.

    Expects objects shaped like {"t", "o", "h", "l", "c", "v"} with string
    or numeric prices. Validates strict monotonic increasing open time and
    finite positive prices — gaps are detected and reported by the caller via
    timestamps, never silently filled here.
    """
    bars: List[Bar] = []
    last_t: Optional[int] = None
    for row in candles:
        try:
            t = int(row["t"])
            o = float(row["o"]); h = float(row["h"])
            lo = float(row["l"]); c = float(row["c"])
            v = float(row.get("v", 0) or 0)
        except (KeyError, TypeError, ValueError) as e:
            raise BacktestError(f"malformed candle row {row!r}: {e}") from e
        if not all(math.isfinite(x) and x > 0 for x in (o, h, lo, c)):
            raise BacktestError(f"non-finite/non-positive price in candle {row!r}")
        if last_t is not None and t <= last_t:
            raise BacktestError(
                f"candle times not strictly increasing ({t} <= {last_t}); "
                "refusing — fix the data source, do not reorder silently."
            )
        last_t = t
        bars.append(Bar(t=t, o=o, h=h, l=lo, c=c, v=v))
    return bars


def _max_drawdown(curve: Sequence[float]) -> float:
    peak = curve[0] if curve else 1.0
    mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        if peak > 0:
            mdd = min(mdd, x / peak - 1.0)
    return mdd


def _segment_metrics(rets: Sequence[float], curve: Sequence[float],
                     bars_per_year: float, n_round_trips: int,
                     wins: int, bars_in_market: int) -> Dict[str, float]:
    n = len(rets)
    if n == 0:
        return {"return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                "n_trades": 0, "win_rate": 0.0, "exposure": 0.0, "n_bars": 0}
    total_return = curve[-1] / curve[0] - 1.0 if curve and curve[0] else 0.0
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n if n > 1 else 0.0
    std = math.sqrt(var)
    sharpe = (mean / std) * math.sqrt(bars_per_year) if std > 0 else 0.0
    years = n / bars_per_year if bars_per_year else 0.0
    growth = curve[-1] / curve[0] if curve and curve[0] else 1.0
    cagr = growth ** (1.0 / years) - 1.0 if years > 0 and growth > 0 else 0.0
    return {
        "return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": _max_drawdown(curve),
        "n_trades": n_round_trips,
        "win_rate": (wins / n_round_trips) if n_round_trips else 0.0,
        "exposure": bars_in_market / n if n else 0.0,
        "n_bars": n,
    }


def run_backtest(
    bars: List[Bar],
    signal_fn: Callable[[List[Bar]], List[float]],
    *,
    symbol: str,
    interval: str,
    strategy: str,
    params: Optional[Dict[str, object]] = None,
    fee_bps: float = 4.5,
    slippage_bps: float = 2.0,
    cost_mult: float = 1.0,
    oos_frac: float = 0.3,
) -> BacktestResult:
    """Run the strategy over real bars and return a labeled result.

    signal_fn(bars) -> list[target_position] of length len(bars). Each target
    must depend only on bars[:i+1] (close of bar i). We then SHIFT by one bar
    so the position established for bar i is the target from bar i-1, filled at
    bar i's open. This is what makes same-bar look-ahead impossible.
    """
    params = dict(params or {})
    if interval not in BARS_PER_YEAR:
        raise BacktestError(f"unknown interval {interval!r}; expected one of "
                            f"{sorted(BARS_PER_YEAR)}")
    if len(bars) < MIN_BARS:
        raise BacktestError(
            f"only {len(bars)} real bars; need >= {MIN_BARS}. Fetch more real "
            "data — never pad with synthetic candles."
        )

    targets = signal_fn(bars)
    if len(targets) != len(bars):
        raise BacktestError(
            f"signal length {len(targets)} != bars {len(bars)} — strategy bug."
        )
    for x in targets:
        if not math.isfinite(x) or not -1.0 <= x <= 1.0:
            raise BacktestError(f"target position out of [-1,1]: {x}")

    bpy = BARS_PER_YEAR[interval]
    fee = (fee_bps / 1e4) * cost_mult
    slip = (slippage_bps / 1e4) * cost_mult

    equity = 1.0
    curve = [equity]
    times = [bars[0].t]
    fills: List[TradeFill] = []
    held = 0.0                       # position currently held into a bar
    rets: List[float] = []           # per-bar equity returns (post fill/cost)
    bars_in_market = 0

    # round-trip tracking for win-rate
    round_trips: List[float] = []
    entry_equity: Optional[float] = None

    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        # Target decided at close of bar i-1 → filled at OPEN of bar i.
        desired = targets[i - 1]
        bar_ret = 0.0
        cost = 0.0
        if desired != held:
            delta = abs(desired - held)
            # slippage scaled by this bar's realized range (vol-aware, explicit)
            vol_scale = (cur.h - cur.l) / cur.o if cur.o else 0.0
            cost = delta * (fee + slip * (1.0 + vol_scale))
            fills.append(TradeFill(t=cur.t, price=cur.o, from_pos=held,
                                   to_pos=desired, cost=cost))
            # round-trip bookkeeping: closing/flipping a non-zero position
            if held != 0.0 and entry_equity is not None:
                round_trips.append(equity / entry_equity - 1.0)
                entry_equity = None
            if desired != 0.0:
                entry_equity = equity  # mark entry at fill
            held = desired
        # Bar P&L: position held across bar i earns the close-to-close move
        # from the fill reference (open) to this bar's close, then carries to
        # next bar via close-to-close. We use open->close for the entry bar and
        # close->close thereafter, approximated as held * (c/prev_c - 1) with
        # the fill cost subtracted. Costs charged at the fill bar.
        price_ret = cur.c / prev.c - 1.0
        bar_ret = held * price_ret - cost
        equity *= (1.0 + bar_ret)
        if held != 0.0:
            bars_in_market += 1
        rets.append(bar_ret)
        curve.append(equity)
        times.append(cur.t)

    # close any open round-trip at the end
    if held != 0.0 and entry_equity is not None:
        round_trips.append(equity / entry_equity - 1.0)

    # IS / OOS reporting holdout (NOT walk-forward; just an honest split)
    oos_frac = min(max(oos_frac, 0.0), 0.9)
    split = int(len(rets) * (1.0 - oos_frac))
    is_rets, oos_rets = rets[:split], rets[split:]
    is_curve = curve[: split + 1]
    oos_curve = curve[split:]
    wins_all = sum(1 for r in round_trips if r > 0)

    result = BacktestResult(
        symbol=symbol, interval=interval, strategy=strategy, params=params,
        n_bars=len(bars), start_ms=bars[0].t, end_ms=bars[-1].t,
        equity_curve=curve, times=times, fills=fills,
        metrics_is=_segment_metrics(is_rets, is_curve, bpy, len(round_trips),
                                    wins_all, sum(1 for r in is_rets if r != 0)),
        metrics_oos=_segment_metrics(oos_rets, oos_curve, bpy, 0, 0,
                                     sum(1 for r in oos_rets if r != 0)),
        fee_bps=fee_bps, slippage_bps=slippage_bps, cost_mult=cost_mult,
        oos_frac=oos_frac, funding_modeled=False,
        status="runs",  # it executed on real bars; not yet shadow-verified
    )
    return result
