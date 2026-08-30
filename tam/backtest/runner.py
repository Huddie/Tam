"""Config-driven backtest runner -- the reusable core behind both the CLI
(examples/backtest.py, kept as a thin argparse wrapper around this module)
and notebook usage (run_backtest() below). Every strategy is just an entry
in `backtest.strategies`, built purely from config via
tam.registry.Registry(Strategy, name) -- this module has no strategy-specific
imports at all. See tam/strategy/*.py for the strategies available out of the
box and tam/backtest/config.py for how a spec becomes a Strategy+Portfolio
pair.

How results get shown (Rich progress + HTML file vs. no bars + inline chart)
is pulled out behind the Presenter interface (see presenter.py) -- this
module drives a backtest and hands the result to whichever Presenter it's
given; it never branches on "am I in a notebook" itself.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path

from ..config import Config
from ..data.providers import DataProvider
from ..data.repository import DataRepository
from ..data.storage import DataStore
from ..portfolio.costs import CostModel
from ..registry import Registry
from .config import build_strategies
from .harness import BacktestHarness
from .presenter import Presenter
from .report import Report
from .visualization import BUY_COLOR, SELL_COLOR, RenderOptions


class DataSettings:
    provider: str
    store: str
    root: str
    provider_kwargs: dict = None


class ReportSettings:
    """Optional top-level `report:` config section -- everything here is
    None/unset by default, so a config that omits it entirely (every example
    config as of this writing) behaves exactly as before. `presenter` names
    an entry in Registry(Presenter, ...) -- "cli"/"clear_output"/"native_dash"
    ship built in (see presenter.py); a project can register its own with
    @Registry.register(Presenter, "my_presenter") and reference it the same
    way, no code change needed here."""

    presenter: str = None
    presenter_kwargs: dict = None
    poll_seconds: float = None
    show_trades_default: bool = None
    height: int = None
    template: str = None


class BacktestSettings:
    tickers: list
    start: str
    end: str
    cash: float
    report_path: str
    strategies: list
    checkpoint_path: str = None
    checkpoint_every: int = 1
    price_chart: dict = None
    cost_model: dict = None


def _build_repository(data_settings: DataSettings) -> DataRepository:
    """`data.provider_kwargs: {...}` (e.g. `{adjust: true}` for
    YFinanceProvider) passes straight through to the provider's own
    constructor -- omitted (every config as of this writing) means a
    zero-arg-constructed provider, unchanged from before this existed."""
    provider = Registry.create(DataProvider, data_settings.provider, **(data_settings.provider_kwargs or {}))
    store = Registry.create(DataStore, data_settings.store, data_settings.root)
    return DataRepository(provider, store)


def _build_cost_model(backtest_settings: BacktestSettings) -> CostModel | None:
    """`backtest.cost_model: {name: ..., **kwargs}` -- omitted (the default
    for every existing config) means None, which Portfolio itself turns into
    ZeroCost (today's exact behavior, unchanged)."""
    if not backtest_settings.cost_model:
        return None
    spec = dict(backtest_settings.cost_model)
    name = spec.pop("name")
    return Registry.create(CostModel, name, **spec)


def _build_render_options(report_settings: ReportSettings, **overrides) -> RenderOptions:
    fields = {
        field: getattr(report_settings, field)
        for field in ("show_trades_default", "height", "template")
        if getattr(report_settings, field) is not None
    }
    fields.update({field: value for field, value in overrides.items() if value is not None})
    return replace(RenderOptions(), **fields)


def _build_presenter(
    name: str, report_settings: ReportSettings, render_options: RenderOptions | None = None, **explicit_kwargs
) -> Presenter:
    """Construct a Presenter by its Registry(Presenter, ...) name, with
    `report_settings.presenter_kwargs` as the base and `explicit_kwargs`
    (whatever the caller passed directly -- run()'s always-required
    report_path, or run_backtest()'s optional presenter_kwargs= override)
    taking priority on any overlapping key. Callers resolve `name` and
    `render_options` themselves first, since precedence between config and an
    explicit Python argument differs between run() (config always wins if
    set) and run_backtest() (an explicit render_mode/show_trades_default
    argument wins over config)."""
    kwargs = {**(report_settings.presenter_kwargs or {}), **explicit_kwargs}
    if report_settings.poll_seconds is not None:
        kwargs.setdefault("poll_seconds", report_settings.poll_seconds)
    kwargs.setdefault(
        "render_options", render_options if render_options is not None else _build_render_options(report_settings)
    )
    try:
        return Registry.create(Presenter, name, **kwargs)
    except KeyError:
        raise ValueError(
            f"report.presenter/render_mode must be one of {sorted(Registry.names(Presenter))}, got {name!r}"
        ) from None


def _config_hash(resolved: dict) -> str:
    canonical = json.dumps(resolved, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _artifacts_dir(config_path: Path, config_hash: str) -> Path:
    """Every config gets its own artifacts folder, keyed by a hash of its
    fully RESOLVED contents (after base/include/vars resolution -- see
    tam/config.py) -- not the top-level file's raw bytes. A config that pulls
    in a shared block via `<< shared/foo.yaml#bar` changes its effective
    contents the moment that shared file changes, even though the top-level
    file's own bytes don't -- hashing only the top-level file's bytes would
    silently keep resuming an old run's checkpoint/adapters under materially
    different settings, with no record of which generation was trained under
    which. So two configs (or two edited versions of the same config, or the
    same config after an included file changes) never collide on the same
    checkpoint/adapter/log files just because they happen to share a
    filename -- a resolved-content change gets a fresh folder rather than
    silently resuming/reusing another run's state. Rooted next to the config
    file itself (config_path.parent / "output"), not a hardcoded
    "examples/output" relative to cwd -- otherwise a config living anywhere
    else (e.g. a test's tmp_path) would write real files into this repo's
    examples/output/ instead of staying self-contained."""
    return config_path.parent / "output" / config_path.stem / config_hash


def _ephemeral_checkpoint_path() -> str:
    """live mode still needs *some* checkpoint file -- it's the only channel
    the live dashboard has to poll the background run's progress -- but with
    --no-save (CLI) or when a notebook call doesn't set one explicitly, it
    belongs in a throwaway tempdir, not the config's persistent artifacts
    dir, and isn't meant to survive a crash for resuming: a fresh path every
    run, cleaned up by the OS eventually, not something anyone would know to
    point --checkpoint-path at afterward."""
    return str(Path(tempfile.mkdtemp(prefix="tam_live_")) / "checkpoint.pkl")


def _apply_artifact_defaults(backtest_settings: BacktestSettings, artifacts_dir: Path) -> None:
    """Fill in checkpoint_path / each llm_trading strategy's log_path and
    lora.adapter_root from the config-hash-namespaced artifacts dir, but only
    where the config didn't already say explicitly -- an explicit value in
    the YAML always wins."""
    if not backtest_settings.checkpoint_path:
        backtest_settings.checkpoint_path = str(artifacts_dir / "checkpoint.pkl")

    for spec in backtest_settings.strategies:
        if spec.strategy == "llm_trading" and "log_path" not in spec.params:
            spec.params.setdefault("log_path", str(artifacts_dir / "llm_log" / f"{spec.portfolio_id}.csv"))
        lora = spec.params.get("lora")
        if lora is not None and "adapter_root" not in lora:
            lora.setdefault("adapter_root", str(artifacts_dir / "lora_adapters" / spec.portfolio_id))


def _ticker_colors(backtest_settings: BacktestSettings) -> dict:
    """{ticker: color} for trade-marker coloring, derived from each strategy's
    own long_ticker/short_ticker config -- not hardcoded ticker names. A
    strategy without that concept (e.g. buy_and_hold) just contributes
    nothing, and its markers fall back to the portfolio's own line color."""
    colors = {}
    for spec in backtest_settings.strategies:
        long_ticker = spec.params.get("long_ticker")
        short_ticker = spec.params.get("short_ticker")
        if long_ticker:
            colors[long_ticker] = BUY_COLOR
        if short_ticker:
            colors[short_ticker] = SELL_COLOR
    return colors


def _collect_price_series(
    repository: DataRepository, backtest_settings: BacktestSettings, start: date, end: date
) -> dict:
    """{ticker: close-price Series} for the optional price panel above the
    equity chart, if `backtest.price_chart.tickers` is set -- {} (chart
    omitted entirely) otherwise, since this is opt-in, not automatic."""
    if not backtest_settings.price_chart:
        return {}
    tickers = list(backtest_settings.price_chart.get("tickers", []))
    return {ticker: repository.query(ticker, start, end)["close"] for ticker in tickers}


def _validate_tickers_declared(backtest_settings: BacktestSettings) -> None:
    """Fail loudly at config-load time if a strategy trades a ticker that isn't in
    `backtest.tickers` — catches config drift before it becomes a cryptic crash
    deep inside whichever strategy factory happens to need price data first.
    Checks both a flat `params.ticker` and a `params.tickers` list, since some
    strategies (e.g. trend_rotation) trade more than one ticker at once. Also
    checks `backtest.price_chart.tickers`, the optional price panel's own list."""
    declared = set(backtest_settings.tickers)

    if backtest_settings.price_chart:
        missing = set(backtest_settings.price_chart.get("tickers", [])) - declared
        if missing:
            raise ValueError(
                f"backtest.price_chart uses ticker(s) {sorted(missing)}, which are missing from "
                f"backtest.tickers {sorted(declared)}"
            )

    for spec in backtest_settings.strategies:
        used = set()
        single = spec.params.get("ticker")
        if single:
            used.add(single)
        multiple = spec.params.get("tickers")
        if multiple:
            used.update(multiple)

        missing = used - declared
        if missing:
            raise ValueError(
                f"strategy {spec.strategy!r} (portfolio {spec.portfolio_id!r}) uses ticker(s) "
                f"{sorted(missing)}, which are missing from backtest.tickers {sorted(declared)}"
            )


def _print_banner(config_path: Path, config_hash: str, artifacts_dir: Path) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table as RichTable

    grid = RichTable.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column()
    grid.add_row("Config", str(config_path))
    grid.add_row("Hash", config_hash)
    grid.add_row("Artifacts", str(artifacts_dir))
    Console().print(Panel(grid, title="[bold]Backtest[/bold]", border_style="cyan", expand=False))


def _load(config_path: Path, start_override: date | None = None, end_override: date | None = None):
    """Everything shared by every entry point (CLI run(), notebook
    run_backtest()) up through "the harness is built and ready to run" --
    config resolution, artifact-dir namespacing, ticker validation, data
    ingestion. Returns (harness, total_days, backtest_settings,
    report_settings, price_series, config_hash, artifacts_dir).

    `start_override`/`end_override`, if given, replace `backtest.start`/`.end`
    from the config -- used by walk_forward.py to run the same config over a
    different (train_start, test_end) date range per window without editing
    the config file itself."""
    cfg = Config(config_path)
    config_hash = _config_hash(cfg.to_dict())
    artifacts_dir = _artifacts_dir(config_path, config_hash)

    data_settings = cfg.data(DataSettings)
    backtest_settings = cfg.backtest(BacktestSettings)
    report_settings = cfg.report(ReportSettings) if "report" in cfg else ReportSettings()
    _validate_tickers_declared(backtest_settings)
    _apply_artifact_defaults(backtest_settings, artifacts_dir)

    repository = _build_repository(data_settings)
    start = start_override if start_override is not None else date.fromisoformat(backtest_settings.start)
    end = end_override if end_override is not None else date.fromisoformat(backtest_settings.end)

    tickers = list(backtest_settings.tickers)
    repository.ingest(tickers, start, end)
    history = repository.query(tickers[0], start, end)
    dates = [ts.date() for ts in history.index]
    price_series = _collect_price_series(repository, backtest_settings, start, end)

    strategies, portfolios, traders = build_strategies(
        repository,
        backtest_settings.strategies,
        float(backtest_settings.cash),
        cost_model=_build_cost_model(backtest_settings),
    )
    harness = BacktestHarness(repository, strategies, portfolios, dates, traders=traders)

    return harness, len(dates), backtest_settings, report_settings, price_series, config_hash, artifacts_dir


def _drive(
    presenter: Presenter,
    harness: BacktestHarness,
    total_days: int,
    backtest_settings: BacktestSettings,
    config_path: Path,
    price_series: dict,
    live: bool,
    port: int,
    verbose: bool,
) -> Report | None:
    """Everything after "harness is built, presenter is chosen" -- neither
    run() nor run_backtest() know or care whether they're driving a
    CliPresenter or a NotebookPresenter past this point."""
    title = f"Backtest{' (live)' if live else ''}: {config_path.stem}"
    ticker_colors = _ticker_colors(backtest_settings)

    if live:
        if not backtest_settings.checkpoint_path:
            raise ValueError("live mode requires backtest.checkpoint_path to be set in the config")
        presenter.run_live(
            harness,
            total_days,
            backtest_settings.checkpoint_path,
            backtest_settings.checkpoint_every,
            title,
            ticker_colors,
            price_series,
            port,
            verbose,
        )
        return None

    report = presenter.run_batch(
        harness, total_days, backtest_settings.checkpoint_path, backtest_settings.checkpoint_every
    )
    presenter.show_report(report, title, ticker_colors, price_series)
    return report


def run(
    config_path: Path,
    mode: str = "batch",
    verbose: bool = False,
    port: int = 8050,
    no_save: bool = False,
    presenter: Presenter | None = None,
) -> None:
    """CLI entry point (examples/backtest.py) -- prints a Rich progress UI,
    writes the final report to backtest.report_path, and (mode="live") serves
    a browser dashboard. For notebook/programmatic use where you want the
    Report object back and an inline chart instead, see run_backtest().

    `presenter`, if given, is used as-is -- any object conforming to the
    Presenter interface (presenter.py), registered or not. Otherwise the
    config's own `report.presenter` (defaulting to "cli") is looked up in
    Registry(Presenter, ...) -- see ReportSettings."""
    config_path = Path(config_path)
    harness, total_days, backtest_settings, report_settings, price_series, config_hash, artifacts_dir = _load(
        config_path
    )
    _print_banner(config_path, config_hash, artifacts_dir)

    live = mode == "live"
    if no_save:
        backtest_settings.checkpoint_path = _ephemeral_checkpoint_path() if live else None

    if presenter is None:
        name = report_settings.presenter or "cli"
        presenter = _build_presenter(name, report_settings, report_path=backtest_settings.report_path)
    _drive(presenter, harness, total_days, backtest_settings, config_path, price_series, live, port, verbose)


def run_backtest(
    config_path,
    live: bool = True,
    verbose: bool = False,
    port: int = 8050,
    render_mode: str | None = None,
    presenter_kwargs: dict | None = None,
    show_trades_default: bool | None = False,
    presenter: Presenter | None = None,
    checkpoint: bool = False,
) -> Report | None:
    """Notebook/programmatic entry point -- same config-driven backtest as
    run() (the CLI's entry point), but tailored for interactive use instead
    of a terminal: no Rich progress UI, and the resulting chart renders
    directly in the current cell's output instead of being written to an
    HTML file for you to separately open.

    live=True (default): starts the backtest on a background thread and
    redraws the SAME chart directly in the current cell every few seconds
    (poll_seconds defaults to 5.0 -- see presenter_kwargs below) as the
    backtest progresses. Returns None immediately (the chart keeps updating
    asynchronously in the output area; there's no single Report yet at the
    moment this call returns). Needs the `notebook` extra (IPython) outside
    a real notebook kernel.

    live=False: runs to completion, displays the final chart via Plotly's
    own rich-display protocol (the same thing fig.show() uses -- renders
    inline in Jupyter/Colab automatically, no extra setup needed there), and
    returns the Report so you can also call report.summary_all(),
    report.to_frame(), etc. yourself. Assign the result to a variable
    (`report = run_backtest(..., live=False)`) rather than leaving the call
    as a cell's bare last expression -- otherwise the notebook also
    auto-echoes the Report's repr underneath the chart.

    checkpoint=False (default): the backtest writes its progress to a
    throwaway temp file, not the config's own persistent, resumable
    checkpoint path (which -- unless `backtest.checkpoint_path` is set
    explicitly -- lives next to the config file itself, e.g. on a
    Drive-mounted directory in Colab; reading/writing that rapidly from two
    threads, as live=True's polling does, over a slow/laggy mount is a real
    source of flaky FileNotFoundErrors, not just a style preference).
    checkpoint=True switches back to that persistent path, so an
    interrupted run can be resumed by calling run_backtest() again with the
    same config -- worth it for a long, unattended run; not needed for
    quick interactive iteration, which is why it isn't the default here
    (unlike the CLI's run(), which defaults to persistent checkpointing and
    opts OUT via --no-save instead).

    `presenter`, if given, is used as-is -- any object conforming to the
    Presenter interface (presenter.py), registered or not; this is the
    escape hatch for a fully custom presentation, no `@Registry.register`
    needed. Everything below only applies when `presenter` is omitted.

    render_mode picks which Presenter (see presenter.py), by name, from
    Registry(Presenter, ...) -- defaults to the config's own `report.presenter`
    (see ReportSettings), falling back to "clear_output" if that's unset too:
      - "clear_output" (default): IPython's clear_output()/display() --
        NOT a real Dash server (unlike --mode live on the CLI, which does
        use one for a real browser tab). Dash's own inline-notebook support
        depends on correctly detecting a hosted notebook's reverse proxy,
        which Colab specifically doesn't support (confirmed both by Dash's
        own source -- jupyter_dash.infer_jupyter_proxy_config is a
        documented no-op "when ... in_colab" -- and empirically: a
        Dash-backed attempt here rendered nothing at all). This is also why
        it's clear_output()-based rather than display(display_id=...)/
        update_display() -- Colab's frontend doesn't reliably replace rich
        HTML/JS content (e.g. a Plotly figure) in place via update_display
        either (confirmed empirically: it kept stacking a new chart
        underneath the old one).
      - "native_dash": DashNotebookPresenter -- the real-Dash-server
        approach, embedded via jupyter_mode="inline" instead of a browser
        tab. Needs the `live` extra (`pip install "tam-quant[live]"` --
        NOT the `notebook` extra, which only adds IPython). Kept available
        for classic Jupyter/JupyterLab (where Dash's own docs describe this
        as fully supported) or in case Colab's own support improves later.
      - anything self-registered via @Registry.register(Presenter, "name")
        (e.g. in your own notebook cell, before calling run_backtest) --
        Registry has no notion of "built in" vs. "yours".

    presenter_kwargs, if given, is merged over the config's own
    `report.presenter_kwargs` and passed straight through to the chosen
    Presenter's constructor -- e.g. render_mode="native_dash",
    presenter_kwargs={"jupyter_mode": "external"} to fall back to a
    clickable link instead of an iframe, or presenter_kwargs=
    {"poll_seconds": 2.0} to refresh more often than the default 5.0s --
    same as setting `report.poll_seconds` in the config, just from Python.
    See each Presenter class in presenter.py for what it accepts.

    show_trades_default overrides the config's own `report.show_trades_default`
    (both default to False here): whether the equity chart's trade markers
    start shown or hidden -- either way, the "Show/Hide Trades" toggle
    button still lets a viewer switch it themselves after the fact.
    """
    config_path = Path(config_path)
    harness, total_days, backtest_settings, report_settings, price_series, _config_hash, _artifacts_dir = _load(
        config_path
    )

    if not checkpoint:
        backtest_settings.checkpoint_path = _ephemeral_checkpoint_path() if live else None

    if presenter is None:
        name = render_mode or report_settings.presenter or "clear_output"
        render_options = _build_render_options(report_settings, show_trades_default=show_trades_default)
        effective_presenter_kwargs = presenter_kwargs if presenter_kwargs is not None else {"poll_seconds": 5.0}
        presenter = _build_presenter(name, report_settings, render_options=render_options, **effective_presenter_kwargs)

    return _drive(presenter, harness, total_days, backtest_settings, config_path, price_series, live, port, verbose)
