# Grover Market Terminal

`grover_terminal.py` is a MoonDev-style terminal cockpit for this repo. It gives you two workspaces in one TUI:

1. **Live News + Metrics** — six panels aligned in a 3x2 grid:
   - live financial / crypto news stream
   - Crypto Fear & Greed index
   - BTC dominance and global crypto market stats
   - Hyperliquid/MoonDev price, funding, OI, and HLP sentiment panel
   - multi-exchange liquidation stats
   - live chain/API events and warnings
2. **Terminal Grid** — four subprocess panes running repo examples by default:
   - `python examples/01_liquidations.py`
   - `python examples/07_orderflow.py`
   - `python examples/17_hlp_sentiment.py`
   - `python examples/19_market_data.py`

## Install

```bash
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and fill in what you have:

```bash
MOONDEV_API_KEY=your_key_here
NEWSAPI_API_KEY=optional_newsapi_key
COINGECKO_API_KEY=optional_coingecko_key
GROVER_REFRESH_SEC=30
GROVER_NEWS_SEC=90
```

RSS feeds work without a news key. `NEWSAPI_API_KEY` only adds broader article search. Hyperliquid/MoonDev panels require `MOONDEV_API_KEY`; when it is missing, the terminal displays `Not Connected / Configure MOONDEV_API_KEY` instead of fake data.

## Run

```bash
python grover_terminal.py
```

## Keys

- `r` — refresh all feeds
- `x` — restart terminal-grid subprocess panes
- `q` — quit

## Custom terminal grid commands

Use either a JSON list:

```bash
GROVER_TERMINAL_COMMANDS='["python examples/14_multi_liquidations.py", "python examples/19_market_data.py"]'
```

Or split shell commands with `||`:

```bash
GROVER_TERMINAL_COMMANDS="python examples/01_liquidations.py || python examples/19_market_data.py"
```

## Data wiring

- Fear & Greed: `https://api.alternative.me/fng/`
- BTC dominance/global crypto: `https://api.coingecko.com/api/v3/global`
- News: RSS by default; optional `https://newsapi.org/v2/everything`
- Hyperliquid/MoonDev: existing `MoonDevAPI` methods in `api.py`

The app uses header auth for optional NewsAPI and CoinGecko keys and never renders secret-bearing URLs in the UI.
