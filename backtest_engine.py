#!/usr/bin/env python3
"""Local-only Backtesting.py engine for Hyperliquid/MoonDev OHLCV data.

This module intentionally never calls live APIs. Feed it local CSV or Parquet
candles only, then review the generated stats/trades/equity artifacts.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from backtesting import Backtest, Strategy
from backtesting.lib import crossover

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
TIME_ALIASES = ("Datetime", "Date", "Time", "Timestamp", "datetime", "date", "time", "timestamp", "t")


def sma(values: pd.Series, period: int) -> pd.Series:
    """Simple moving average compatible with Backtesting.py Strategy.I."""
    return pd.Series(values).rolling(period).mean()


def rsi(values: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-style RSI using pandas rolling means."""
    close = pd.Series(values)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


class SmaCross(Strategy):
    """Baseline moving-average cross strategy for smoke tests and comparisons."""

    n_fast = 10
    n_slow = 30
    allow_short = True

    def init(self) -> None:
        close = self.data.Close
        self.fast = self.I(sma, close, self.n_fast)
        self.slow = self.I(sma, close, self.n_slow)

    def next(self) -> None:
        if crossover(self.fast, self.slow):
            self.position.close()
            self.buy()
        elif crossover(self.slow, self.fast):
            self.position.close()
            if self.allow_short:
                self.sell()


class RsiMeanReversion(Strategy):
    """Simple RSI mean-reversion baseline; useful as a second validation control."""

    period = 14
    lower = 30
    upper = 70
    allow_short = False

    def init(self) -> None:
        self.rsi = self.I(rsi, self.data.Close, self.period)

    def next(self) -> None:
        latest = self.rsi[-1]
        if math.isnan(latest):
            return
        if latest < self.lower and not self.position.is_long:
            self.position.close()
            self.buy()
        elif latest > self.upper:
            if self.position.is_long:
                self.position.close()
            elif self.allow_short and not self.position.is_short:
                self.sell()


class BuyAndHold(Strategy):
    """Buy once and hold. This gives a baseline against Backtesting.py stats."""

    def init(self) -> None:
        self._entered = False

    def next(self) -> None:
        if not self._entered:
            self.buy()
            self._entered = True


STRATEGIES: dict[str, type[Strategy]] = {
    "sma_cross": SmaCross,
    "rsi_mean_reversion": RsiMeanReversion,
    "buy_hold": BuyAndHold,
}


@dataclass(frozen=True)
class BacktestConfig:
    data: Path
    strategy: str = "sma_cross"
    cash: float = 10_000.0
    commission: float = 0.001
    margin: float = 1.0
    exclusive_orders: bool = True
    trade_on_close: bool = False
    hedging: bool = False
    output_dir: Path = Path("reports/backtests")
    plot: bool = False
    optimize: bool = False
    maximize: str = "Sharpe Ratio"


def read_ohlcv(path: Path) -> pd.DataFrame:
    """Load and normalize local OHLCV data from CSV or Parquet.

    Accepted column aliases are case-insensitive: open/o, high/h, low/l,
    close/c, volume/vol/v, and time/date/timestamp/t for the datetime index.
    """
    if not path.exists():
        raise FileNotFoundError(f"Data file does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw = pd.read_csv(path)
    elif suffix in {".parquet", ".pq"}:
        raw = pd.read_parquet(path)
    else:
        raise ValueError("Unsupported input. Use .csv, .parquet, or .pq")
    if raw.empty:
        raise ValueError("Input data is empty")
    return normalize_ohlcv(raw)


def normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """Return a Backtesting.py-compatible OHLCV DataFrame."""
    df = raw.copy()
    column_map: dict[str, str] = {}
    for col in df.columns:
        compact = str(col).strip().lower().replace("_", "").replace(" ", "")
        if compact in {"open", "o"}:
            column_map[col] = "Open"
        elif compact in {"high", "h"}:
            column_map[col] = "High"
        elif compact in {"low", "l"}:
            column_map[col] = "Low"
        elif compact in {"close", "c", "price"}:
            column_map[col] = "Close"
        elif compact in {"volume", "vol", "v"}:
            column_map[col] = "Volume"
        elif compact in {"datetime", "date", "time", "timestamp", "t", "opentime"}:
            column_map[col] = "Datetime"
    df = df.rename(columns=column_map)

    if "Datetime" in df.columns:
        series = df["Datetime"]
        if pd.api.types.is_numeric_dtype(series):
            unit = "ms" if series.dropna().astype(float).median() > 10_000_000_000 else "s"
            df.index = pd.to_datetime(series, unit=unit, utc=True).dt.tz_convert(None)
        else:
            df.index = pd.to_datetime(series, utc=True, errors="coerce").dt.tz_convert(None)
        df = df.drop(columns=["Datetime"])
    elif not isinstance(df.index, pd.DatetimeIndex):
        for name in TIME_ALIASES:
            if name in raw.columns:
                df.index = pd.to_datetime(raw[name], utc=True, errors="coerce").dt.tz_convert(None)
                break

    missing = [col for col in ["Open", "High", "Low", "Close"] if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required OHLC columns: {', '.join(missing)}")
    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df = df[OHLCV_COLUMNS].copy()
    for col in OHLCV_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df["Volume"] = df["Volume"].fillna(0.0)

    if isinstance(df.index, pd.DatetimeIndex):
        df = df[~df.index.isna()].sort_index()
        df = df[~df.index.duplicated(keep="last")]
    else:
        df = df.reset_index(drop=True)

    invalid = (df["High"] < df[["Open", "Close", "Low"]].max(axis=1)) | (df["Low"] > df[["Open", "Close", "High"]].min(axis=1))
    if bool(invalid.any()):
        raise ValueError(f"Invalid OHLC relationships in {int(invalid.sum())} row(s)")
    if len(df) < 50:
        raise ValueError("Need at least 50 candles for a meaningful engine smoke test")
    return df


def make_backtest(config: BacktestConfig, data: pd.DataFrame) -> Backtest:
    strategy = STRATEGIES.get(config.strategy)
    if strategy is None:
        raise ValueError(f"Unknown strategy '{config.strategy}'. Choices: {', '.join(STRATEGIES)}")
    return Backtest(
        data,
        strategy,
        cash=config.cash,
        commission=config.commission,
        margin=config.margin,
        trade_on_close=config.trade_on_close,
        hedging=config.hedging,
        exclusive_orders=config.exclusive_orders,
    )


def run_engine(config: BacktestConfig) -> dict[str, Any]:
    data = read_ohlcv(config.data)
    bt = make_backtest(config, data)
    stats = optimize(bt, config) if config.optimize and config.strategy == "sma_cross" else bt.run()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{config.data.stem}_{config.strategy}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    stats_path = config.output_dir / f"{stem}_stats.json"
    trades_path = config.output_dir / f"{stem}_trades.csv"
    equity_path = config.output_dir / f"{stem}_equity.csv"
    plot_path = config.output_dir / f"{stem}.html"

    serial = serialize_stats(stats)
    serial["config"] = {**asdict(config), "data": str(config.data), "output_dir": str(config.output_dir)}
    serial["data_rows"] = len(data)
    serial["data_start"] = str(data.index.min()) if isinstance(data.index, pd.DatetimeIndex) else "0"
    serial["data_end"] = str(data.index.max()) if isinstance(data.index, pd.DatetimeIndex) else str(len(data) - 1)
    stats_path.write_text(json.dumps(serial, indent=2, sort_keys=True), encoding="utf-8")

    trades = stats.get("_trades")
    if isinstance(trades, pd.DataFrame):
        trades.to_csv(trades_path, index=False)
    equity = stats.get("_equity_curve")
    if isinstance(equity, pd.DataFrame):
        equity.to_csv(equity_path)

    if config.plot:
        try:
            bt.plot(filename=str(plot_path), open_browser=False)
        except TypeError:
            bt.plot(filename=str(plot_path))

    return {
        "stats": serial,
        "stats_path": str(stats_path),
        "trades_path": str(trades_path) if trades_path.exists() else None,
        "equity_path": str(equity_path) if equity_path.exists() else None,
        "plot_path": str(plot_path) if plot_path.exists() else None,
    }


def optimize(bt: Backtest, config: BacktestConfig) -> pd.Series:
    """Small, constrained SMA sweep. Keep broader WFO/Monte Carlo in separate modules."""
    return bt.optimize(
        n_fast=range(5, 35, 5),
        n_slow=range(20, 121, 10),
        maximize=config.maximize,
        constraint=lambda params: params.n_fast < params.n_slow,
    )


def serialize_stats(stats: pd.Series) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in stats.items():
        if str(key).startswith("_"):
            continue
        out[str(key)] = scalar(value)
    return out


def scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if hasattr(value, "item"):
        try:
            return scalar(value.item())
        except Exception:
            pass
    if pd.isna(value):
        return None
    return str(value)


def parse_args(argv: list[str] | None = None) -> BacktestConfig:
    parser = argparse.ArgumentParser(description="Run a local-only Backtesting.py backtest on OHLCV data.")
    parser.add_argument("--data", required=True, type=Path, help="Local CSV/Parquet OHLCV file")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="sma_cross")
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--commission", type=float, default=0.001, help="Fractional commission, e.g. 0.001 = 0.10%")
    parser.add_argument("--margin", type=float, default=1.0, help="1.0 = no leverage; 0.1 = 10x margin")
    parser.add_argument("--trade-on-close", action="store_true")
    parser.add_argument("--hedging", action="store_true")
    parser.add_argument("--non-exclusive-orders", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/backtests"))
    parser.add_argument("--plot", action="store_true", help="Write an interactive Backtesting.py HTML plot")
    parser.add_argument("--optimize", action="store_true", help="Run constrained SMA optimization for sma_cross")
    parser.add_argument("--maximize", default="Sharpe Ratio", help="Metric for optimization")
    args = parser.parse_args(argv)
    return BacktestConfig(
        data=args.data,
        strategy=args.strategy,
        cash=args.cash,
        commission=args.commission,
        margin=args.margin,
        exclusive_orders=not args.non_exclusive_orders,
        trade_on_close=args.trade_on_close,
        hedging=args.hedging,
        output_dir=args.output_dir,
        plot=args.plot,
        optimize=args.optimize,
        maximize=args.maximize,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        result = run_engine(parse_args(argv))
    except Exception as exc:
        print(f"BACKTEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    stats = result["stats"]
    print("Backtest complete")
    print(f"Stats:  {result['stats_path']}")
    if result.get("trades_path"):
        print(f"Trades: {result['trades_path']}")
    if result.get("equity_path"):
        print(f"Equity: {result['equity_path']}")
    if result.get("plot_path"):
        print(f"Plot:   {result['plot_path']}")
    print(json.dumps({k: stats.get(k) for k in ["Return [%]", "Sharpe Ratio", "Max. Drawdown [%]", "# Trades", "Win Rate [%]"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
