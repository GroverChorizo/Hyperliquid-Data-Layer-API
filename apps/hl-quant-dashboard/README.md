# HL Quant Dashboard

A local-first web app for **visualizing Hyperliquid data** (via the MoonDev
Data Layer API) and running **backtests in one place** that **export markdown
reports into an Obsidian vault**.

> Read-only research phase. No orders, no live trading, not financial advice.
> **Real data or an explicit error — never synthetic.** Funding is not yet
> modeled in backtests (engine gap, surfaced in every report).

It is intentionally a single small Python server (standard library only) plus
a dependency-free vanilla-JS dashboard. No web framework, no CDN, no build
step. The MoonDev API key stays server-side and never reaches the browser.

## Layout

```
apps/hl-quant-dashboard/
  server.py            stdlib HTTP server: dashboard + /api proxy + backtest
  moondev_client.py    server-side wrapper over the repo's MoonDevAPI client
  backtest/
    engine.py          next-bar-open fills, explicit costs, real-data-only
    strategies.py      transparent example strategies (sma_cross, donchian)
    vault_export.py    renders an Obsidian markdown note for a run
  dashboard/           index.html + app.js + style.css (cyberpunk dark)
  vault/               default Obsidian vault target (Backtests/ notes land here)
```

## Setup

1. Install the repo's client deps (from the repo root):

   ```bash
   pip install -r requirements.txt   # requests, rich, python-dotenv, pandas
   ```

2. Add your MoonDev API key to a `.env` file at the **repo root** (the existing
   client loads it). Never commit it.

   ```bash
   MOONDEV_API_KEY=your_api_key_here
   ```

   Get a key at https://moondev.com.

## Run

```bash
cd apps/hl-quant-dashboard
python server.py            # http://127.0.0.1:8787
```

Then open the URL. The header shows whether a key is set and which vault path
is active. Without a key, data panels show an explicit "real data unavailable"
state — by design there is no fake fallback.

Environment overrides:

| var | default | purpose |
| --- | --- | --- |
| `HLQD_HOST` | `127.0.0.1` | bind host (keep local) |
| `HLQD_PORT` | `8787` | bind port |
| `QUANT_VAULT_PATH` | `./vault` | where exported backtest notes are written |
| `MOONDEV_API_KEY` | — | real data access (loaded by the client) |

## What's here

- **Markets** — real OHLCV candles (canvas chart) + prices / funding / OI table.
- **Liquidations / Whales** — recent real events as compact tables.
- **HLP / Smart Money** — raw real payloads (renderers WIP).
- **Backtest** — pick a coin / interval / strategy / params, run on real
  candles, see the equity curve + IS/OOS metrics, then **Export to vault**.

## Backtest contract (honest by construction)

- Fills at the **next bar open** after the signal bar closes (no same-bar
  look-ahead — the engine shifts the target series by one bar).
- Costs are explicit: taker fee (bps) + vol-scaled slippage (bps), with a cost
  multiplier so you can re-run at 2x costs.
- The engine **refuses** to run on fewer than 60 real bars and never pads with
  synthetic candles.
- IS/OOS is a chronological **holdout for reporting only** — it is *not*
  walk-forward validation. Promote nothing on a single run. OOS Sharpe > 3 is
  flagged as a likely bug, not an edge.

## Status

`untested` against live data in this checkout (no API key was present when it
was authored). The server boots and serves; data paths and backtests have been
exercised only against honest no-key / no-data error states. Run it with a real
key to move it to `runs`.

## Notes / TODO

- Funding series not modeled in the engine (tracked gap).
- Walk-forward + permutation (Monte Carlo) validation not yet wired in.
- HLP / smart-money domain renderers are raw JSON for now.
- This subproject is staged inside the data-layer repo for now; it's intended
  to graduate into its own repository.
