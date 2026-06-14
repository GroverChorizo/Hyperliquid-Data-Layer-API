# Quant Vault (default target)

This folder is the default **Obsidian vault** target for the HL Quant
Dashboard. Open it directly in Obsidian (File → Open vault → this folder), or
point the app at your real vault with `QUANT_VAULT_PATH`.

- `Backtests/` — one markdown note per exported backtest run, with YAML
  front-matter (`symbol`, `timeframe`, `strategy`, `status`, `bars`,
  `funding_modeled`) so Obsidian can index and query them.

Every note is a research artifact: not financial advice, funding not modeled,
and IS/OOS is a reporting holdout — not walk-forward validation.
