"""LLM-driven rotation: each night, asks a locally-served language model to learn
how the Nasdaq (tracked via QQQ) tends to trade from a broad set of technical
signals, and to express its view as a target net exposure percentage -- not a
binary call. TQQQ/SQQQ are purely the vehicle for expressing that view (3x
long / 3x short), the same way trend_rotation.py and ml_walk_forward.py signal
off the clean underlying and trade the leveraged pair; the model never sees
TQQQ/SQQQ's own price action, only QQQ's.

Talks to whatever's actually serving the model over HTTP using the OpenAI-
compatible /chat/completions shape, since that's the closest thing to a
universal local-LLM-serving convention today (Ollama, llama.cpp's server,
LM Studio, and others all speak it) -- this avoids hard-coding to one specific
SDK. Swap `base_url`/`model`, or inject a different `llm_client` callable
entirely, to point at whichever server is actually running.

Signal design, deliberately *not* pre-digested into a buy/sell rule -- the
model is meant to learn the relationship between these raw signals and
forward returns itself, the same way trend_rotation.py's SMA/momentum filters
encode a relationship a human quant chose by hand. Signals are pluggable: see
signals.py -- each is registered as @Registry.register(Signal, "id") and
exposes a name, a plain-language description (handed to the model alongside
its values, so it knows what the number means), and compute(close). This
strategy just assembles whichever signals its `signals` config lists (or a
broad built-in default set spanning trend/mean-reversion/momentum/volatility)
-- it has no special knowledge of any one signal's meaning. Each signal is
shown as its own trailing history (last `history_window` days, configurable),
not just today's value, so the model can see whether it's rising, falling, or
oscillating -- not just where it happens to sit today.

Output/sizing: the model responds with a single number in [-100, 100] --
positive = that percentage of the portfolio in TQQQ (bullish), negative =
that percentage (absolute value) in SQQQ (bearish), 0 = cash. A percentage
output (vs. a forced LONG/SHORT word) lets the model express low conviction
by staying near 0 instead of being forced to fully commit to a side every
day, which is what makes small day-to-day changes in view cheap (a small
resize) instead of always a full liquidate-and-reverse.

On "learning over time": always does honest in-context adaptation (each
prompt includes the model's own recent calls, the hindsight-optimal exposure
each one turned out to be, and its recent calibration error) regardless of
whether real weight updates are happening. If `llm_client` also exposes a
`record_outcome` method, this strategy calls it once per realized outcome
with the hindsight-optimal percentage formatted exactly like the number the
model is asked to produce -- so a client that DOES perform real (periodic,
not per-day) weight updates -- see mlx_lora_client.py -- trains on the same
task format it's asked to perform at inference, not a proxy for it. If a
call fails (server down, timeout, unparseable response) the strategy falls
back to holding whatever exposure it already holds rather than crashing the
run, or to cash if nothing has ever been held yet.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Callable, List, Optional

import pandas as pd
import requests

from ..data.repository import DataRepository
from ..events.clock import EOD_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, Qty, QtyBasis, Side
from ..registry import Registry
from .base import Strategy
from .signals import Signal, build_signals

LLMClient = Callable[[str], str]

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# A broad default spanning trend, mean reversion, momentum, and volatility --
# used when a config doesn't specify its own `signals:` list. See signals.py
# for what each one means; nothing here is specific to this strategy.
DEFAULT_SIGNAL_SPECS = [
    {"id": "sma", "configs": [{"window": w} for w in (10, 50, 100, 200, 300)]},
    {"id": "zscore", "configs": [{"window": w} for w in (20, 50)]},
    {"id": "rsi", "config": {"period": 14}},
    {"id": "return", "configs": [{"horizon": h} for h in (1, 5, 20, 60)]},
    {"id": "volatility", "config": {"window": 20}},
    {"id": "macd"},
    {"id": "bollinger_pct_b"},
    {"id": "distance_from_high", "config": {"window": 252}},
]

_SYSTEM_PROMPT = (
    "You learn how the Nasdaq (QQQ) trades from technical signals. You trade via "
    "TQQQ (3x long) and SQQQ (3x short), not QQQ directly. Output a target net "
    "exposure: + means % of portfolio in TQQQ, - means % in SQQQ, 0 means cash. "
    "Low conviction should look like a number near 0. Respond with ONLY a number "
    "in [-100, 100]."
)


def _http_llm_client(base_url: str, model: str, timeout: float = 30.0) -> LLMClient:
    def call(prompt: str) -> str:
        response = requests.post(
            base_url,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "stream": False,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    return call


class LLMTradingStrategy(Strategy):
    def __init__(
        self,
        repository: DataRepository,
        signal_ticker: str,
        long_ticker: str,
        short_ticker: str,
        sell_qty,
        portfolio_id: str,
        llm_client: Optional[LLMClient] = None,
        base_url: str = "http://localhost:11434/v1/chat/completions",
        model: str = "qwen2.5:7b",
        memory_window: int = 10,
        signals: Optional[List[Signal]] = None,
        history_window: int = 20,
        rebalance_threshold_pct: float = 5.0,
        target_daily_vol: float = 0.01,
    ):
        super().__init__()
        self._repository = repository
        self._signal_ticker = signal_ticker
        self._long_ticker = long_ticker
        self._short_ticker = short_ticker
        self._sell_qty = Qty.of(sell_qty)
        self._portfolio_id = portfolio_id
        self._llm_client = llm_client or _http_llm_client(base_url, model)
        self._memory = deque(maxlen=memory_window)
        self._signals = signals if signals is not None else build_signals(DEFAULT_SIGNAL_SPECS)
        self._history_window = history_window
        self._rebalance_threshold_pct = rebalance_threshold_pct
        self._target_daily_vol = target_daily_vol
        # Query enough history for every signal to be able to fully populate once
        # it's warmed up (max), but only *require* the fastest-warming signal (min)
        # before starting to call the model and accumulate training outcomes --
        # slower signals show a placeholder in the prompt until their own window
        # fills, rather than blocking the whole strategy on the slowest one.
        self._max_required_history = max(s.required_history() for s in self._signals) + self._history_window
        self._min_required_history = min(s.required_history() for s in self._signals)
        self._current_pct = 0.0  # signed net exposure: + long TQQQ, - short SQQQ, 0 cash
        self._pending = None  # (prompt, predicted_pct, price_as_of_pending) awaiting tomorrow's outcome

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        as_of = event.payload
        history = self._repository.query(self._signal_ticker, end=as_of).tail(self._max_required_history)
        if len(history) < self._min_required_history:
            return

        close = history["close"]
        current_price = close.iloc[-1]

        if self._pending is not None:
            self._resolve_pending(current_price)

        prompt = self._build_prompt(close)
        target_pct = self._ask_llm(prompt)
        if target_pct is None:
            target_pct = self._current_pct  # fall back to holding current exposure
        self._pending = (prompt, target_pct, current_price)

        self._rebalance_to(target_pct)

    def _resolve_pending(self, current_price: float) -> None:
        pending_prompt, predicted_pct, prior_price = self._pending
        realized_return = current_price / prior_price - 1
        ideal_pct = self._clip(realized_return / self._target_daily_vol * 100)
        self._memory.append({"predicted": predicted_pct, "ideal": ideal_pct, "error": abs(predicted_pct - ideal_pct)})

        record_outcome = getattr(self._llm_client, "record_outcome", None)
        if record_outcome is not None:
            record_outcome(pending_prompt, f"{ideal_pct:+.0f}")

    @staticmethod
    def _clip(pct: float) -> float:
        return max(-100.0, min(100.0, pct))

    def _build_prompt(self, close: pd.Series) -> str:
        signal_lines = []
        for signal in self._signals:
            values = signal.compute(close).tail(self._history_window)
            if values.empty:
                body = f"n/a (needs {signal.required_history()}d)"
            else:
                body = f"last {len(values)}d: " + ", ".join(f"{v:.4f}" for v in values)
            signal_lines.append(f"- {signal.name}: {signal.description}\n  {body}")
        signal_block = "\n".join(signal_lines)

        if self._memory:
            track_record = "\n".join(
                f"- {m['predicted']:+.0f}% vs ideal {m['ideal']:+.0f}% (err {m['error']:.0f})"
                for m in self._memory
            )
            mae = sum(m["error"] for m in self._memory) / len(self._memory)
            track_record += f"\nMAE: {mae:.0f} over last {len(self._memory)}."
        else:
            track_record = "(none yet)"

        return (
            f"Nasdaq (QQQ) signals:\n{signal_block}\n\n"
            f"Calibration:\n{track_record}\n\n"
            "Respond with ONLY a number in [-100, 100] -- your suggested net exposure."
        )

    def _ask_llm(self, prompt: str) -> Optional[float]:
        try:
            raw = self._llm_client(prompt)
        except Exception:
            return None

        match = _NUMBER_RE.search(raw)
        if match is None:
            return None
        return self._clip(float(match.group()))

    def _rebalance_to(self, target_pct: float) -> None:
        target_pct = self._clip(target_pct)
        if abs(target_pct - self._current_pct) < self._rebalance_threshold_pct:
            return

        current_ticker = self._ticker_for(self._current_pct)
        if current_ticker is not None:
            self.trade.stocks(
                [Order(ticker=current_ticker, side=Side.SELL, qty=self._sell_qty, portfolio=self._portfolio_id)]
            )

        target_ticker = self._ticker_for(target_pct)
        if target_ticker is not None:
            buy_qty = Qty(pct=abs(target_pct), basis=QtyBasis.CASH)
            self.trade.stocks(
                [Order(ticker=target_ticker, side=Side.BUY, qty=buy_qty, portfolio=self._portfolio_id)]
            )
        self._current_pct = target_pct

    def _ticker_for(self, pct: float) -> Optional[str]:
        if pct > 0:
            return self._long_ticker
        if pct < 0:
            return self._short_ticker
        return None

    def get_state(self) -> dict:
        state = {
            "current_pct": self._current_pct,
            "pending": self._pending,
            "memory": list(self._memory),
        }
        client_get_state = getattr(self._llm_client, "get_state", None)
        if client_get_state is not None:
            state["llm_client"] = client_get_state()
        return state

    def load_state(self, state: dict) -> None:
        self._current_pct = state["current_pct"]
        self._pending = state["pending"]
        self._memory = deque(state["memory"], maxlen=self._memory.maxlen)
        client_load_state = getattr(self._llm_client, "load_state", None)
        if client_load_state is not None and "llm_client" in state:
            client_load_state(state["llm_client"])


@Registry.register(Strategy, "llm_trading")
def build_llm_trading(repository: DataRepository, portfolio_id: str, params, cash: float) -> LLMTradingStrategy:
    sell_qty = params["sell"]["qty"] if "sell" in params else params.get("qty", {"pct": 100})

    llm_client = None
    lora_params = params.get("lora")
    if lora_params:
        from .mlx_lora_client import MLXLoRAClient

        llm_client = MLXLoRAClient(
            model=lora_params.get("model", MLXLoRAClient.DEFAULT_MODEL),
            system_prompt=_SYSTEM_PROMPT,
            fine_tune_every_n_days=lora_params.get("fine_tune_every_n_days", 20),
            iters=lora_params.get("iters", 50),
            learning_rate=lora_params.get("learning_rate", 1e-5),
            adapter_root=lora_params.get("adapter_root"),
        )

    return LLMTradingStrategy(
        repository,
        params["signal_ticker"],
        params["long_ticker"],
        params["short_ticker"],
        sell_qty,
        portfolio_id,
        llm_client=llm_client,
        base_url=params.get("base_url", "http://localhost:11434/v1/chat/completions"),
        model=params.get("model", "qwen2.5:7b"),
        memory_window=params.get("memory_window", 10),
        signals=build_signals(params.get("signals") or DEFAULT_SIGNAL_SPECS),
        history_window=params.get("history_window", 20),
        rebalance_threshold_pct=params.get("rebalance_threshold_pct", 5.0),
        target_daily_vol=params.get("target_daily_vol", 0.01),
    )
