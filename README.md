# tam

Config-driven event backtesting for stocks/indices: YAML in, an interactive
HTML dashboard out. Strategies (moving-average, MA crossover, trend rotation,
online-learning ML, local-LLM) are all pluggable — `examples/backtest.py`
doesn't import any strategy directly, it builds whatever's listed in the
config's `strategies:` section by name.

Published on PyPI as `tam-quant` (`pip install tam-quant`; `import tam` either
way). Running in Google Colab or Jupyter instead of this repo's own CLI? See
[NOTEBOOK.md](NOTEBOOK.md). Want to use individual pieces (data fetching,
rendering, live updates) outside the config-driven runner, or see every
component's API at a glance? See [LIB.md](LIB.md).

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.11 (pinned in
`.python-version` — `uv` will fetch it automatically if you don't have it).

```
uv sync --extra dev
```

This creates `.venv/` and installs everything, including dev dependencies
(pytest). Run any command below with `uv run ...` so it uses that environment
— no need to activate the venv manually.

If you want to use the FMP data provider instead of the (no-key-needed)
yfinance default, copy `.env.example` to `.env` and fill in `FMP_API_KEY`.

## Running the examples

Each example is a YAML config passed to the same runner:

```
uv run python -m examples.backtest examples/moving_average_config.yaml
uv run python -m examples.backtest examples/ma_crossover_config.yaml
uv run python -m examples.backtest examples/trend_rotation_config.yaml
```

These three work out of the box — no extra setup, no external services. Each
run prints a summary table (returns, Sharpe, drawdown, etc. per strategy)
with a live progress bar, and writes an interactive HTML dashboard to
`examples/output/<name>_report.html` — open that in a browser to see the
equity curves, drawdown, and per-trade markers (toggle with the "Show
Trades" button).

`examples/llm_trading_config.yaml` is different: it drives a strategy that
queries a local language model each simulated day, and by default also
periodically LoRA fine-tunes it (both via `mlx-lm`, M-series Mac only). The
first run downloads the base model from Hugging Face (needs network once).
Because it calls the model every simulated day, this one is much slower than
the others — try a short date range first (edit `start`/`end` in the config)
before running the full period. See the comments in that file for how to
point it at Ollama or another server instead, or turn LoRA fine-tuning off.

Want to try your own mix of strategies? Copy one of the configs and edit its
`strategies:` list — see `tam/strategy/*.py` for what's registered and what
params each one takes.

## Running the tests

```
uv run pytest
```

No network access or external services required — everything is tested
against fakes/mocks (fake data providers, a stubbed LLM client, etc.).
