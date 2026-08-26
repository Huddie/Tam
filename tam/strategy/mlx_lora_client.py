"""Local LLM client that periodically LoRA fine-tunes itself on realized
outcomes, for use as the `llm_client` in llm_trading.py's LLMTradingStrategy.

Runs entirely locally via mlx-lm (M-series Mac / Metal) -- no network calls,
no Ollama. Genuinely updates the model's weights every `fine_tune_every_n_days`
accumulated outcomes, unlike LLMTradingStrategy's always-on in-context memory
(which stays active on top of this -- the two are complementary, not
alternatives). This is real gradient-based training, so it costs real
wall-clock time per pass (expect tens of seconds to a few minutes even for a
small model/LoRA rank on an M-series Mac) -- pick a period large enough that
this doesn't dominate backtest runtime. Defaults to a small (~0.5B parameter)
instruct model for the same reason: fast enough to fine-tune repeatedly
inside a backtest loop, unlike the 7B model llm_trading.py defaults to for
pure inference via Ollama.

Training target: the *realized* outcome for each prompt (what actually
happened), not the model's own prior guess -- that's the actual supervised
signal available, and it's what lets the model correct its mistakes rather
than only reinforcing whatever it already happened to get right.

Every mlx_lm-specific knob (lora rank/dropout/scale, optimizer/weight decay,
num_layers, fine_tune_type, ...) is passed through to `mlx_lm.lora` via an
actual YAML config file matching mlx_lm's own schema (`-c config.yaml`),
rather than hand-built `--flag value` argv pairs -- so a knob mlx_lm supports
but this client doesn't have a named parameter for is still reachable via
`extra_mlx_config`, without inventing a parallel argv-building path for it.
See _mlx_lm_config for the full field list.

===============================================================================
Every round trains from the base model, on ALL realized outcomes so far
===============================================================================

Every realized outcome ever recorded is kept forever in `self._history` (see
record_outcome) -- never discarded after a fine-tune round. Each round trains
a FRESH LoRA adapter from the unmodified base model (no resume_adapter_file),
using the full accumulated history as training data, chronologically
train/validation-split (see _split_for_validation) so a round is always
evaluated on the most-recent slice it hasn't trained on, without ever using
information from a day before that day's outcome was actually realized (no
lookahead bias -- record_outcome for day N only ever fires after day N's
return has actually happened, so nothing here changes when information
becomes available, only how much of it stays retained across rounds).

This was NOT always the design. An earlier version cleared the buffer after
every round and always resumed the previous round's adapter weights --
training each round only on the handful of days since the last one, while
carrying cumulative weight-level "memory" forward indefinitely. Two problems,
observed directly in real backtests:
  - Regime amnesia: a round trained only on its own narrow recent window has
    no way to remember older, different regimes -- the model re-learns "the
    recent window's majority pattern" every round and forgets everything
    before it, producing a model that behaves like a coarse regime-switcher
    tied to the fine-tune schedule rather than a function of the daily
    signals it's actually shown (confirmed empirically: measured real
    next-token probabilities barely move across genuinely different days
    under a fixed adapter -- the day-to-day signal has some real influence,
    but not remotely enough to flip greedy decoding's top choice; only an
    actual weight update -- i.e. a fine-tune round -- was ever large enough
    to do that).
  - Unbounded compounding: resuming forever meant generation N had absorbed
    N x iters gradient steps on the same small set of LoRA weights, with
    nothing ever resetting -- not N independent attempts at the task, one
    continuously-drifting weight state that a long enough run could walk
    into a degenerate attractor (observed directly: real backtests where the
    model settled into repeating one fixed token, e.g. "!!!!!!!!!!!!!!!!",
    on every inference call, forever, because a short/low-entropy output is
    a loss-minimum the training loss rewards just as readily as a genuinely
    correct one).

Training on the full accumulated history from a fresh base model each round
addresses the first problem structurally (nothing to forget -- the training
set itself carries history forward, not the weights) and eliminates the
second problem's actual mechanism (no resuming means no unbounded compounding
across rounds -- each round is an independent full retrain, not one endlessly
continued run). The regularization and detection below are kept regardless,
as defense in depth against a single bad round -- full-history training
doesn't guarantee any one training run can't still go wrong.

===============================================================================
What's a genuine mlx_lm gap, not fixable via config
===============================================================================

mlx_lm.lora has NO gradient-norm clipping and NO NaN/inf detection anywhere
in its training loop (confirmed by reading mlx_lm/tuner/trainer.py -- there's
no clip/grad_norm/isnan code path, and no CLI flag or YAML key for it). A
framework like HF's Trainer defaults to clipping gradients at norm 1.0;
mlx_lm simply doesn't offer an equivalent, so a genuinely exploding gradient
inside one training run can't be caught or limited from the outside while
it's happening -- extra_mlx_config has no such key to set because mlx_lm has
nothing to set it on. Given that, the levers actually available are:

  (a) Regularization, defaulted to nonzero unlike mlx_lm's own zero defaults:
      lora_dropout (0.05) and weight_decay (0.01, via the adamw optimizer --
      plain "adam" in mlx has no weight_decay parameter at all, so it's only
      applied when optimizer="adamw"). Directly fights a round settling into
      an overconfident, low-entropy attractor.
  (b) Detection, applied to every freshly fine-tuned adapter before it's
      trusted (see _fine_tune):
        - _training_diverged: this round's own held-out validation loss (see
          val_split) tripling or going NaN/inf is treated as a training-time
          red flag, without even needing to run inference to find out.
        - _adapter_is_healthy: re-fires a sample of recent real prompts at
          the new adapter and requires the response still parses as exactly
          one number -- the same bar llm_trading.py holds live inference
          responses to.
      A generation that fails either check is discarded (deleted from disk)
      and _current_adapter stays on the last known-good adapter.

For anyone importing generic SFT-debugging advice (e.g. "check your prompt/
label masking", "check your chat template") into this specific stack, both
are already handled entirely inside mlx_lm itself, not hand-rolled here:
mask_prompt=True computes a token offset from re-tokenizing messages[:-1]
and masks every loss position outside [offset, real_length) (mlx_lm/tuner/
{datasets,trainer}.py); training data (mlx_lm's ChatDataset) and inference
(__call__ below) both go through the SAME tokenizer's own
apply_chat_template, so there's no separately hand-written template anywhere
in this file that could drift from the tokenizer's real one.

===============================================================================
Configuration guide -- constructor parameters, grouped by what they trade off
===============================================================================
  fine_tune_every_n_days: NOT an mlx_lm setting, and not known to
      LLMTradingStrategy either -- it's this client's own scheduling
      parameter, one layer above both. LLMTradingStrategy calls
      record_outcome() once per realized trading day, unconditionally; this
      client decides internally how many of those accumulated outcomes
      justify actually spawning an mlx_lm.lora subprocess (see
      record_outcome below). Never appears in _mlx_lm_config's output --
      mlx_lm has no concept of it. Larger -> fewer, less-frequent rounds
      (each one training on the same ever-growing history) -- less wall-clock
      overhead but slower to react to a real regime change.
  iters / learning_rate: the actual mlx_lm training-run dose -- both go
      straight into _mlx_lm_config. Since every round now starts fresh from
      the base model (no resuming), these no longer compound across rounds
      the way they used to -- each round is an independent retrain, not one
      continued run, so there's more room to raise either than there used to
      be. iters is independent of how large `history` has grown (mlx_lm just
      loops/samples batches for exactly `iters` steps regardless of dataset
      size), so a growing history does not by itself make rounds slower.
  max_seq_length: must cover the full prompt + completion in tokens or mlx_lm
      silently truncates training sequences to it -- if that cuts off before
      the assistant's turn, every example trains on zero completion tokens
      (a silent "Trained Tokens 0" / NaN loss), not a crash. This is now
      caught for you -- _check_completion_token_budget measures every NEW
      example each round against the tokenizer's real chat template before
      training starts and raises immediately if any would lose its
      completion to truncation, rather than relying on a comment recording
      what one example happened to measure at whatever history_window/
      memory_window were in use at the time (that number goes stale the
      moment either changes).
  batch_size: mlx_lm's own default (4) can OOM at a long max_seq_length even
      with grad_checkpoint on; drop to 1 once max_seq_length grows past a
      couple thousand tokens.
  grad_checkpoint: trades training speed for memory.
  num_layers / fine_tune_type: how much of the model is actually trainable
      ("lora"/"dora" wrap linear layers in the last `num_layers`; "full"
      unfreezes real weights outright -- much more collapse-prone, since
      there's no small adapter subspace constraining the update).
  lora_rank / lora_scale: LoRA adapter capacity and effective-update
      magnitude -- higher rank/scale = more expressive but more prone to
      overfitting.
  lora_dropout / optimizer / weight_decay: the regularization levers from
      (a) above.
  val_split: fraction of the full accumulated history held out (the most
      recent slice, chronologically) to give mlx_lm a real Val loss to
      report (see (b) above) -- 0 disables validation entirely, matching
      mlx_lm's own no-valid.jsonl behavior.
  max_val_examples: hard cap on the held-out set's size regardless of
      val_split -- without this, a fixed fraction of an ever-growing history
      means both the held-out set and its per-round evaluation cost (mlx_lm
      evaluates the whole thing steps_per_eval times per round) grow without
      bound over a long backtest, making every round slower than the last.
      None disables the cap (val_split alone decides the size).
  extra_mlx_config: escape hatch for any mlx_lm.lora YAML key without its own
      named parameter here (e.g. `seed`) -- deep-merged over everything else,
      so it can override a nested key like lora_parameters without repeating
      its untouched siblings.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..status import report

_ITER_RE = re.compile(r"Iter (\d+): (.*)")
_TRAIN_LOSS_RE = re.compile(r"Train loss ([\w.+-]+)")
_VAL_LOSS_RE = re.compile(r"Val loss ([\w.+-]+)")
# Same convention llm_trading.py parses responses with -- a fine-tune whose
# adapter can't reproduce this on its own training prompts has collapsed,
# not just had an off day (see _adapter_is_healthy).
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into `base` (override wins on collision)
    -- used to splice `extra_mlx_config` over this client's own settings, so
    a caller can override (or add to) a nested key like lora_parameters
    without having to repeat the rest of it."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class MLXLoRAClient:
    DEFAULT_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
    # How many of the most recent real prompts to re-fire at a freshly
    # fine-tuned adapter before trusting it (see _adapter_is_healthy).
    _VALIDATION_SAMPLE_SIZE = 5
    # A validation-set fine-tune needs at least this many total examples
    # before holding any out is worth it -- below this, val_split is a no-op
    # and everything goes to training (see _split_for_validation).
    _MIN_EXAMPLES_FOR_VALIDATION_SPLIT = 5

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        system_prompt: str = "",
        fine_tune_every_n_days: int = 20,
        adapter_root: Optional[str] = None,
        iters: int = 50,
        learning_rate: float = 1e-5,
        grad_checkpoint: bool = True,
        max_seq_length: int = 4096,
        batch_size: Optional[int] = None,
        num_layers: int = 16,
        fine_tune_type: str = "lora",
        lora_rank: int = 8,
        lora_dropout: float = 0.05,
        lora_scale: float = 20.0,
        optimizer: str = "adamw",
        weight_decay: float = 0.01,
        val_split: float = 0.2,
        max_val_examples: Optional[int] = 100,
        extra_mlx_config: Optional[dict] = None,
    ):
        self._model_path = model
        self._system_prompt = system_prompt
        self._fine_tune_every_n_days = fine_tune_every_n_days
        self._adapter_root = Path(adapter_root or tempfile.mkdtemp(prefix="fin_lora_"))
        self._adapter_root.mkdir(parents=True, exist_ok=True)
        self._iters = iters
        self._learning_rate = learning_rate
        self._grad_checkpoint = grad_checkpoint
        self._max_seq_length = max_seq_length
        self._batch_size = batch_size
        self._num_layers = num_layers
        self._fine_tune_type = fine_tune_type
        self._lora_rank = lora_rank
        self._lora_dropout = lora_dropout
        self._lora_scale = lora_scale
        self._optimizer = optimizer
        self._weight_decay = weight_decay
        self._val_split = val_split
        self._max_val_examples = max_val_examples
        self._extra_mlx_config = extra_mlx_config or {}

        self._model = None
        self._tokenizer = None
        self._current_adapter = self._latest_generation()
        # Every realized outcome ever recorded, oldest first, never cleared
        # -- see the module docstring for why this replaced a per-round
        # buffer that got cleared after each fine-tune.
        self._history: List[Tuple[str, str]] = []
        self._days_since_fine_tune = 0
        self._generation = int(self._current_adapter.name.split("_")[1]) if self._current_adapter else 0
        # (iteration, val_loss) pairs from the most recent _run_training call
        # -- an early-warning divergence signal, reset per fine-tune round
        # (see _training_diverged). Not persisted: it's a diagnostic for the
        # round just run, not state that needs to survive a restart.
        self._last_val_losses: List[Tuple[int, float]] = []

    def _latest_generation(self) -> Optional[Path]:
        """Resume from whatever's already on disk under adapter_root, if anything --
        so a fresh process (e.g. after a crash/restart) doesn't silently drop back to
        the un-fine-tuned base model despite prior generations being saved."""
        candidates = []
        for path in self._adapter_root.glob("gen_*"):
            if not (path / "adapters.safetensors").exists():
                continue
            try:
                candidates.append((int(path.name.split("_")[1]), path))
            except (IndexError, ValueError):
                continue
        return max(candidates, key=lambda pair: pair[0])[1] if candidates else None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from huggingface_hub.utils import disable_progress_bars
        from mlx_lm.utils import load

        disable_progress_bars()  # cache hits print a "Fetching/Reconstructing" bar every time otherwise
        report(f"loading {self._model_path}...")
        start = time.time()
        adapter_path = str(self._current_adapter) if self._current_adapter is not None else None
        self._model, self._tokenizer = load(self._model_path, adapter_path=adapter_path)
        report(f"loaded {self._model_path} in {time.time() - start:.1f}s")

    def _messages_for(self, prompt: str, completion: Optional[str] = None) -> list:
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": prompt})
        if completion is not None:
            messages.append({"role": "assistant", "content": completion})
        return messages

    def __call__(self, prompt: str) -> str:
        self._ensure_loaded()
        from mlx_lm import generate

        templated = self._tokenizer.apply_chat_template(
            self._messages_for(prompt), tokenize=False, add_generation_prompt=True
        )
        return generate(self._model, self._tokenizer, prompt=templated, verbose=False, max_tokens=16)

    @property
    def generation(self) -> int:
        """Bumped by a successful _fine_tune() pass, reverted on a discarded
        (failed-validation) one -- see _fine_tune. Public so a caller (e.g.
        LLMTradingStrategy) can diff it across a record_outcome() call to
        tell "fine-tuned" apart from "tried and discarded"."""
        return self._generation

    def record_outcome(self, prompt: str, realized_side: str) -> None:
        self._history.append((prompt, realized_side.upper()))
        self._days_since_fine_tune += 1
        if self._days_since_fine_tune >= self._fine_tune_every_n_days:
            self._fine_tune()
            self._days_since_fine_tune = 0

    def _split_for_validation(self, history: List[Tuple[str, str]]) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        """Holds out `val_split` of the FULL accumulated history so mlx_lm
        can report a real Val loss during training (see _training_diverged)
        -- the most RECENT examples become the validation set, not a random
        sample. These are temporally ordered realized trading outcomes with
        real autocorrelation (regime clustering); a random split would
        validate on interpolating within the same period, which isn't the
        actual deployment scenario -- a fixed adapter predicting forward in
        time until the next fine-tune. history is already in chronological
        order (record_outcome appends once per realized day), so this is
        just "earliest examples train, latest validate." Below
        _MIN_EXAMPLES_FOR_VALIDATION_SPLIT examples, holding any out isn't
        worth shrinking the training set for.

        Capped at `max_val_examples` regardless of how large `val_split *
        len(history)` would otherwise be -- a straight fraction of an
        ever-growing history means both the held-out set AND its per-round
        evaluation cost (mlx_lm evaluates the whole thing `steps_per_eval`
        times per round) grow without bound over a long backtest. A fixed
        cap keeps per-round wall-clock cost roughly constant instead of
        getting slower every single round for the rest of the run."""
        if self._val_split <= 0 or len(history) < self._MIN_EXAMPLES_FOR_VALIDATION_SPLIT:
            return list(history), []
        val_count = max(1, int(len(history) * self._val_split))
        if self._max_val_examples is not None:
            val_count = min(val_count, self._max_val_examples)
        return list(history[:-val_count]), list(history[-val_count:])

    def _write_jsonl(self, path: Path, examples: List[Tuple[str, str]]) -> None:
        with path.open("w") as handle:
            for prompt, realized_side in examples:
                messages = self._messages_for(prompt, completion=realized_side)
                handle.write(json.dumps({"messages": messages}) + "\n")

    def _check_completion_token_budget(self, new_examples: List[Tuple[str, str]]) -> None:
        """mlx_lm truncates every training sequence to max_seq_length -- if
        that cuts off before an example's assistant turn even starts (i.e.
        the prompt alone, tokenized, is already >= max_seq_length), that
        example trains on zero supervised tokens: a silent no-op that
        surfaces only as a "Trained Tokens 0" line or a NaN loss, not an
        error. Catch it here, before ever starting the training subprocess,
        using the exact same prompt/completion token-offset convention
        mlx_lm's own ChatDataset uses (mlx_lm/tuner/datasets.py) -- so this
        mirrors what mlx_lm will actually do, not an approximation of it.

        Only checks the examples new since the last round, not the whole
        (ever-growing) history -- older examples already passed this check
        when they were new, and re-tokenizing the full history every round
        would make each round's overhead grow without bound as a backtest
        progresses. This assumes max_seq_length doesn't shrink mid-run; if it
        does, an already-accepted older example could in principle now
        violate a smaller budget without being re-flagged."""
        max_prompt_tokens = 0
        max_total_tokens = 0
        for prompt, completion in new_examples:
            messages = self._messages_for(prompt, completion=completion)
            total_tokens = len(self._tokenizer.apply_chat_template(messages, return_dict=False))
            prompt_tokens = len(
                self._tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, return_dict=False)
            )
            max_prompt_tokens = max(max_prompt_tokens, prompt_tokens)
            max_total_tokens = max(max_total_tokens, total_tokens)
            surviving_completion_tokens = min(total_tokens, self._max_seq_length) - prompt_tokens
            if surviving_completion_tokens <= 0:
                raise ValueError(
                    f"max_seq_length={self._max_seq_length} is too small for this round's data: "
                    f"one example's prompt alone tokenizes to {prompt_tokens} tokens, leaving zero "
                    f"room for its completion after mlx_lm's truncation. Raise max_seq_length (or "
                    "shrink history_window/memory_window) rather than silently training on 0 "
                    "target tokens -- see _check_completion_token_budget."
                )
        report(
            f"fine-tuning gen {self._generation + 1}: {len(new_examples)} new examples, "
            f"max prompt {max_prompt_tokens} tok, max total {max_total_tokens} tok "
            f"(max_seq_length={self._max_seq_length})"
        )

    def _mlx_lm_config(self, data_dir: Path, new_adapter: Path, has_validation_set: bool) -> Dict[str, Any]:
        """Everything handed to `mlx_lm.lora -c <this>`, in mlx_lm's own
        config schema -- the single place any mlx_lm training knob lives, so
        adding one means adding a field here (or passing it via
        extra_mlx_config) rather than growing a parallel argv-flag builder.
        Deliberately has no resume_adapter_file -- every round trains a
        fresh adapter from the base model (see module docstring)."""
        settings: Dict[str, Any] = {
            "model": self._model_path,
            "train": True,
            "data": str(data_dir),
            "fine_tune_type": self._fine_tune_type,
            "num_layers": self._num_layers,
            "adapter_path": str(new_adapter),
            "iters": self._iters,
            "learning_rate": self._learning_rate,
            # -1 -> evaluate against the entire held-out set each time,
            # rather than requiring an exact batch count for a small
            # per-round buffer; 0 -> no validation set this round, so
            # skip evaluation entirely (matches mlx_lm's own "no valid.jsonl"
            # behavior, just without the warning print).
            "val_batches": -1 if has_validation_set else 0,
            # A few eval points across the run (plus mlx_lm's own always-eval
            # at iter 1) gives _training_diverged something to compare
            # against, without evaluating every single iteration.
            "steps_per_eval": max(1, self._iters // 5),
            # Restricts the loss to the assistant's completion tokens, not
            # the (much longer) prompt -- without this, the model just learns
            # to keep predicting more prompt-shaped content (a dense block
            # of small-decimal signal values) after its actual answer.
            "mask_prompt": True,
            "max_seq_length": self._max_seq_length,
            "grad_checkpoint": self._grad_checkpoint,
            "lora_parameters": {
                "rank": self._lora_rank,
                "dropout": self._lora_dropout,
                "scale": self._lora_scale,
            },
            "optimizer": self._optimizer,
        }
        if self._optimizer == "adamw":
            # mlx's plain Adam has no weight_decay parameter -- only AdamW
            # does; passing it for another optimizer would be a straight
            # constructor error in mlx.optimizers.
            settings["optimizer_config"] = {"adamw": {"weight_decay": self._weight_decay}}
        if self._batch_size is not None:
            settings["batch_size"] = self._batch_size
        return _deep_merge(settings, self._extra_mlx_config)

    def _fine_tune(self) -> None:
        if not self._history:
            return
        self._ensure_loaded()
        # Only the examples new since the last round -- see
        # _check_completion_token_budget's docstring for why.
        self._check_completion_token_budget(self._history[-self._days_since_fine_tune :])

        data_dir = Path(tempfile.mkdtemp(prefix="fin_lora_data_"))
        train_examples, valid_examples = self._split_for_validation(self._history)
        self._write_jsonl(data_dir / "train.jsonl", train_examples)
        if valid_examples:
            self._write_jsonl(data_dir / "valid.jsonl", valid_examples)

        self._generation += 1
        new_adapter = self._adapter_root / f"gen_{self._generation}"

        config_path = data_dir / "mlx_lora_config.yaml"
        config_path.write_text(yaml.safe_dump(self._mlx_lm_config(data_dir, new_adapter, bool(valid_examples))))

        report(
            f"fine-tuning gen {self._generation} on {len(train_examples)} train + "
            f"{len(valid_examples)} val examples ({len(self._history)} total in history)",
            0,
            self._iters,
        )
        start = time.time()
        self._run_training([sys.executable, "-m", "mlx_lm", "lora", "-c", str(config_path)])
        report(f"gen {self._generation} done in {time.time() - start:.1f}s", self._iters, self._iters)

        if self._adapter_is_healthy(new_adapter):
            self._current_adapter = new_adapter
        else:
            # A single bad training run can still overfit/diverge even
            # starting fresh each round (see module docstring) -- without
            # this check that adapter becomes _current_adapter for live
            # inference. Discard it and keep serving the last known-good
            # adapter; the next round trains fresh again regardless (there's
            # no resuming to "carry forward" a discarded round's damage).
            report(f"gen {self._generation} failed validation -- discarding, keeping prior adapter")
            shutil.rmtree(new_adapter, ignore_errors=True)
            self._generation -= 1

        self._model = None  # force a reload with the (possibly unchanged) current adapter on next call

    def _training_diverged(self) -> bool:
        """Early-warning signal from this round's own held-out validation
        loss (see val_split) -- a run whose loss exploded or went NaN/inf is
        diverging, not learning, regardless of what the inference-sample
        check below finds. Only meaningful with at least two eval points (a
        before/after comparison); with fewer (e.g. no validation set this
        round), this can't say anything and defers entirely to
        _adapter_is_healthy's inference check."""
        if len(self._last_val_losses) < 2:
            return False
        losses = [v for _, v in self._last_val_losses]
        if any(math.isnan(v) or math.isinf(v) for v in losses):
            return True
        return losses[-1] > losses[0] * 3.0

    def _adapter_is_healthy(self, adapter_path: Path) -> bool:
        """Re-fires a sample of the most recent real prompts at the freshly
        fine-tuned adapter and checks the response is still exactly one
        parseable number -- the same bar llm_trading.py holds inference
        responses to. A real model has off days and gets the number wrong;
        that's fine, this only screens for the adapter no longer producing a
        number-shaped response at all, which is what mode collapse looks
        like. Also fails immediately on a validation-loss divergence (see
        _training_diverged), without needing to run inference to find out."""
        if self._training_diverged():
            return False

        from mlx_lm import generate
        from mlx_lm.utils import load

        try:
            model, tokenizer = load(self._model_path, adapter_path=str(adapter_path))
        except Exception:
            return False

        sample = self._history[-self._VALIDATION_SAMPLE_SIZE :]
        for prompt, _realized_side in sample:
            templated = tokenizer.apply_chat_template(
                self._messages_for(prompt), tokenize=False, add_generation_prompt=True
            )
            raw = generate(model, tokenizer, prompt=templated, verbose=False, max_tokens=16)
            if len(_NUMBER_RE.findall(raw)) != 1:
                return False
        return True

    def _run_training(self, args: List[str]) -> None:
        """Runs mlx_lm's training CLI as a subprocess, captured rather than
        streamed raw -- "Iter N: ..." lines update a real (current/total)
        sub-progress bar via tam.status instead of scrolling the terminal, but
        the full captured output is dumped on a non-zero exit so a real
        failure is still fully diagnosable, not silently swallowed. Also
        collects (iteration, val_loss) pairs into self._last_val_losses for
        _training_diverged to inspect afterward."""
        import subprocess

        self._last_val_losses = []
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        lines: List[str] = []
        for line in process.stdout:
            lines.append(line)
            match = _ITER_RE.match(line.strip())
            if not match:
                continue
            iteration, detail = int(match.group(1)), match.group(2)
            val_match = _VAL_LOSS_RE.search(detail)
            if val_match:
                self._last_val_losses.append((iteration, float(val_match.group(1))))
                report(f"fine-tuning gen {self._generation}: val loss {val_match.group(1)}", iteration, self._iters)
                continue
            train_match = _TRAIN_LOSS_RE.search(detail)
            loss_text = f"loss {train_match.group(1)}" if train_match else "training"
            report(f"fine-tuning gen {self._generation}: {loss_text}", iteration, self._iters)
        process.wait()

        if process.returncode != 0:
            sys.stderr.write("".join(lines))
            raise subprocess.CalledProcessError(process.returncode, args)

    def get_state(self) -> dict:
        """Everything not already durable on disk under adapter_root -- the
        full outcome history and where we are in the fine-tune cadence -- so
        a crash between fine-tune passes doesn't lose accumulated progress."""
        return {
            "history": list(self._history),
            "days_since_fine_tune": self._days_since_fine_tune,
            "generation": self._generation,
            "current_adapter": str(self._current_adapter) if self._current_adapter else None,
        }

    def load_state(self, state: dict) -> None:
        # "buffer" fallback: a checkpoint written before history stopped
        # being cleared after each round used that key for the same idea
        # (just scoped to one round instead of forever) -- accept it so an
        # in-progress backtest's checkpoint isn't broken by this change.
        self._history = list(state.get("history", state.get("buffer", [])))
        self._days_since_fine_tune = state["days_since_fine_tune"]
        self._generation = state["generation"]
        current_adapter = state["current_adapter"]
        self._current_adapter = Path(current_adapter) if current_adapter else None
        self._model = None  # force a reload so the next call picks up the restored adapter
