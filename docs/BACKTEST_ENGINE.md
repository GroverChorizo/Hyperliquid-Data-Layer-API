# Local Backtesting.py Engine

`backtest_engine.py` adds a local-only backtest runner built on top of [Backtesting.py](https://kernc.github.io/backtesting.py/). It is designed for historical OHLCV files that you already have on disk. It does **not** call MoonDev, Hyperliquid, CoinGecko, or any live API during a backtest.

## Install

```bash
pip install -r requirements.txt
```

## Input data contract

Use CSV or Parquet with at least 50 candles. Required OHLC columns are normalized case-insensitively:

| Required | Accepted aliases |
|---|---|
| `Open` | `open`, `o` |
| `High` | `high`, `h` |
| `Low` | `low`, `l` |
| `Close` | `close`, `c`, `price` |
| `Volume` | `volume`, `vol`, `v`; optional, defaults to `0` |
| `Datetime` | `datetime`, `date`, `time`, `timestamp`, `t`, `open_time` |

The loader validates that the file exists, has non-empty data, contains valid OHLC relationships, sorts timestamps, removes duplicate timestamps, and refuses tiny files below 50 rows.

## Run a baseline backtest

```bash
python backtest_engine.py \
  --data data/BTC_1h.csv \
  --strategy sma_cross \
  --cash 10000 \
  --commission 0.001 \
  --output-dir reports/backtests
```

Outputs:

- `*_stats.json` — summary metrics and config
- `*_trades.csv` — executed trades from Backtesting.py
- `*_equity.csv` — equity curve

## Generate an interactive plot

```bash
python backtest_engine.py --data data/BTC_1h.csv --strategy sma_cross --plot
```

This writes a Backtesting.py HTML chart into `reports/backtests/`.

## Strategies included

- `sma_cross` — moving-average crossover; default baseline.
- `rsi_mean_reversion` — simple RSI control strategy.
- `buy_hold` — buy once and hold; sanity baseline.

These are not production strategies. Treat them as engine smoke tests and controls before adding your real strategy logic.

## Constrained SMA optimization

```bash
python backtest_engine.py \
  --data data/BTC_1h.csv \
  --strategy sma_cross \
  --optimize \
  --maximize "Sharpe Ratio"
```

The optimizer only sweeps `n_fast` and `n_slow` with the constraint `n_fast < n_slow`. Broader parameter mining should be handled by a separate walk-forward / Monte Carlo layer so we do not overfit one historical path.

## Python usage

```python
from pathlib import Path
from backtest_engine import BacktestConfig, run_engine

result = run_engine(BacktestConfig(
    data=Path("data/BTC_1h.csv"),
    strategy="sma_cross",
    cash=10_000,
    commission=0.001,
))

print(result["stats"])
```

## Safety rules

- Backtests are local-only.
- No synthetic data is generated.
- No live API calls are made by `backtest_engine.py`.
- Stats are evaluation artifacts, not trade recommendations.
- Optimization is constrained and should be followed by walk-forward and out-of-sample validation before any paper-trading use.
