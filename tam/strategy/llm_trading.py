"""LLM-driven rotation: each day, asks a locally-served language model for a
LONG/SHORT call on the underlying, based on a compact technical summary --
same long/short rotation mechanic as trend_rotation.py and ml_walk_forward.py.

Talks to whatever's actually serving the model over HTTP using the OpenAI-
compatible /chat/completions shape, since that's the closest thing to a
universal local-LLM-serving convention today (Ollama, llama.cpp's server,
LM Studio, and others all speak it) -- this avoids hard-coding to one specific
SDK. Swap `base_url`/`model`, or inject a different `llm_client` callable
entirely, to point at whichever server is actually running.

On "learning over time": genuinely fine-tuning a multi-billion-parameter
model's weights once per simulated day is impractical -- each pass would cost
real wall-clock minutes, against microseconds for the classical model in
ml_walk_forward.py. This strategy always does honest in-context adaptation:
each prompt includes the model's own recent calls and whether they were
right, so it can condition on its own track record ("reflection" prompting)
without any weight update. If `llm_client` also exposes a `record_outcome`
method, this strategy calls it once per realized outcome, so a client that
DOES perform real (periodic, not per-day) weight updates -- see
mlx_lora_client.py -- learns from it, on top of the in-context memory, which
stays active either way. If a call fails (server down, timeout, unparseable
response) the strategy falls back to holding whatever side it already holds
rather than crashing the run.
"""
from __future__ import annotations

from collections import deque
from typing import Callable, Optional

import requests

from ..data.repository import DataRepository
from ..events.clock import EOD_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, Qty, Side
from ..registry import Registry
from .base import Strategy
from .indicators import rsi, sma

LLMClient = Callable[[str], str]

_SYSTEM_PROMPT = (
    "You are a disciplined systematic trading assistant. Given a technical "
    "summary of an index and your own recent track record, decide whether to "
    "be net LONG or net SHORT for the next trading period. "
    "Respond with exactly one word: LONG or SHORT."
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
        buy_qty,
        sell_qty,
        portfolio_id: str,
        llm_client: Optional[LLMClient] = None,
        base_url: str = "http://localhost:11434/v1/chat/completions",
        model: str = "qwen2.5:7b",
        memory_window: int = 10,
        lookback: int = 20,
    ):
        super().__init__()
        self._repository = repository
        self._signal_ticker = signal_ticker
        self._long_ticker = long_ticker
        self._short_ticker = short_ticker
        self._buy_qty = Qty.of(buy_qty)
        self._sell_qty = Qty.of(sell_qty)
        self._portfolio_id = portfolio_id
        self._llm_client = llm_client or _http_llm_client(base_url, model)
        self._lookback = lookback
        self._memory = deque(maxlen=memory_window)
        self._held = None  # None | "long" | "short"
        self._pending = None  # (prompt, predicted_side, price_as_of_pending) awaiting tomorrow's outcome

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        as_of = event.payload
        history = self._repository.query(self._signal_ticker, end=as_of).tail(self._lookback + 1)
        if len(history) < self._lookback + 1:
            return

        close = history["close"]
        current_price = close.iloc[-1]

        if self._pending is not None:
            pending_prompt, predicted_side, prior_price = self._pending
            actual_side = "long" if current_price > prior_price else "short"
            self._memory.append(
                {"predicted": predicted_side, "actual": actual_side, "correct": predicted_side == actual_side}
            )
            record_outcome = getattr(self._llm_client, "record_outcome", None)
            if record_outcome is not None:
                record_outcome(pending_prompt, actual_side)

        prompt = self._build_prompt(close)
        target = self._ask_llm(prompt) or self._held or "long"
        self._pending = (prompt, target, current_price)

        if target != self._held:
            self._flip_to(target)

    def _summarize(self, close) -> dict:
        price = close.iloc[-1]
        ret_5 = close.iloc[-1] / close.iloc[-6] - 1
        ret_full = close.iloc[-1] / close.iloc[0] - 1
        volatility = close.pct_change().std()
        rsi_period = min(14, len(close) - 1)
        rsi_value = float(rsi(close, rsi_period).iloc[-1]) if rsi_period >= 2 else None
        sma_value = float(sma(close, len(close)).iloc[-1])
        return {
            "price": float(price),
            "return_5d": float(ret_5),
            "return_window": float(ret_full),
            "volatility": float(volatility),
            "rsi": rsi_value,
            "price_vs_sma": float(price / sma_value - 1),
        }

    def _build_prompt(self, close) -> str:
        summary = self._summarize(close)
        summary_lines = "\n".join(f"- {key}: {value:.4f}" for key, value in summary.items() if value is not None)

        if self._memory:
            track_record = "\n".join(
                f"- predicted {m['predicted'].upper()}, actual was {m['actual'].upper()} "
                f"({'correct' if m['correct'] else 'wrong'})"
                for m in self._memory
            )
            hit_rate = sum(m["correct"] for m in self._memory) / len(self._memory)
            track_record += f"\nRecent hit rate: {hit_rate:.0%} over the last {len(self._memory)} calls."
        else:
            track_record = "(no track record yet)"

        return (
            f"Technical summary for {self._signal_ticker}:\n{summary_lines}\n\n"
            f"Your recent track record:\n{track_record}\n\n"
            "Respond with exactly one word: LONG or SHORT."
        )

    def _ask_llm(self, prompt: str) -> Optional[str]:
        try:
            raw = self._llm_client(prompt)
        except Exception:
            return None

        text = raw.strip().upper()
        has_long, has_short = "LONG" in text, "SHORT" in text
        if has_long and not has_short:
            return "long"
        if has_short and not has_long:
            return "short"
        return None

    def _flip_to(self, target: str) -> None:
        exit_ticker = {"long": self._long_ticker, "short": self._short_ticker}.get(self._held)
        if exit_ticker is not None:
            self.trade.stocks(
                [Order(ticker=exit_ticker, side=Side.SELL, qty=self._sell_qty, portfolio=self._portfolio_id)]
            )
        entry_ticker = self._long_ticker if target == "long" else self._short_ticker
        self.trade.stocks(
            [Order(ticker=entry_ticker, side=Side.BUY, qty=self._buy_qty, portfolio=self._portfolio_id)]
        )
        self._held = target


@Registry.register(Strategy, "llm_trading")
def build_llm_trading(repository: DataRepository, portfolio_id: str, params, cash: float) -> LLMTradingStrategy:
    buy_qty = params["buy"]["qty"] if "buy" in params else params["qty"]
    sell_qty = params["sell"]["qty"] if "sell" in params else params["qty"]

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
        buy_qty,
        sell_qty,
        portfolio_id,
        llm_client=llm_client,
        base_url=params.get("base_url", "http://localhost:11434/v1/chat/completions"),
        model=params.get("model", "qwen2.5:7b"),
        memory_window=params.get("memory_window", 10),
        lookback=params.get("lookback", 20),
    )
