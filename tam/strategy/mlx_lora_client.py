"""Local LLM client that periodically LoRA fine-tunes itself on realized
outcomes, for use as the `llm_client` in llm_trading.py's LLMTradingStrategy.

Runs entirely locally via mlx-lm (Apple Silicon / Metal) -- no network calls,
no Ollama. Genuinely updates the model's weights every `fine_tune_every_n_days`
accumulated outcomes, unlike LLMTradingStrategy's always-on in-context memory
(which stays active on top of this -- the two are complementary, not
alternatives). This is real gradient-based training, so it costs real
wall-clock time per pass (expect tens of seconds to a few minutes even for a
small model/LoRA rank on Apple Silicon) -- pick a period large enough that
this doesn't dominate backtest runtime. Defaults to a small (~0.5B parameter)
instruct model for the same reason: fast enough to fine-tune repeatedly
inside a backtest loop, unlike the 7B model llm_trading.py defaults to for
pure inference via Ollama.

Training target: the *realized* outcome for each prompt (what actually
happened), not the model's own prior guess -- that's the actual supervised
signal available, and it's what lets the model correct its mistakes rather
than only reinforcing whatever it already happened to get right.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from ..status import report

_ITER_RE = re.compile(r"Iter (\d+): (.*)")


class MLXLoRAClient:
    DEFAULT_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        system_prompt: str = "",
        fine_tune_every_n_days: int = 20,
        adapter_root: Optional[str] = None,
        iters: int = 50,
        learning_rate: float = 1e-5,
    ):
        self._model_path = model
        self._system_prompt = system_prompt
        self._fine_tune_every_n_days = fine_tune_every_n_days
        self._adapter_root = Path(adapter_root or tempfile.mkdtemp(prefix="fin_lora_"))
        self._adapter_root.mkdir(parents=True, exist_ok=True)
        self._iters = iters
        self._learning_rate = learning_rate

        self._model = None
        self._tokenizer = None
        self._current_adapter = self._latest_generation()
        self._buffer: List[Tuple[str, str]] = []  # (prompt, realized_side)
        self._days_since_fine_tune = 0
        self._generation = int(self._current_adapter.name.split("_")[1]) if self._current_adapter else 0

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

    def record_outcome(self, prompt: str, realized_side: str) -> None:
        self._buffer.append((prompt, realized_side.upper()))
        self._days_since_fine_tune += 1
        if self._days_since_fine_tune >= self._fine_tune_every_n_days:
            self._fine_tune()
            self._days_since_fine_tune = 0

    def _fine_tune(self) -> None:
        if not self._buffer:
            return
        self._ensure_loaded()

        data_dir = Path(tempfile.mkdtemp(prefix="fin_lora_data_"))
        with (data_dir / "train.jsonl").open("w") as handle:
            for prompt, realized_side in self._buffer:
                messages = self._messages_for(prompt, completion=realized_side)
                handle.write(json.dumps({"messages": messages}) + "\n")

        self._generation += 1
        new_adapter = self._adapter_root / f"gen_{self._generation}"

        args = [
            sys.executable,
            "-m",
            "mlx_lm",
            "lora",
            "--model",
            self._model_path,
            "--train",
            "--data",
            str(data_dir),
            "--adapter-path",
            str(new_adapter),
            "--iters",
            str(self._iters),
            "--learning-rate",
            str(self._learning_rate),
            "--val-batches",
            "0",
            "--steps-per-eval",
            str(self._iters + 1),
            "--mask-prompt",
        ]
        resume_from = self._current_adapter / "adapters.safetensors" if self._current_adapter else None
        if resume_from is not None and resume_from.exists():
            args += ["--resume-adapter-file", str(resume_from)]

        report(f"fine-tuning gen {self._generation} on {len(self._buffer)} examples", 0, self._iters)
        start = time.time()
        self._run_training(args)
        report(f"gen {self._generation} done in {time.time() - start:.1f}s", self._iters, self._iters)

        self._current_adapter = new_adapter
        self._buffer.clear()
        self._model = None  # force a reload with the freshly-trained adapter on next call

    def _run_training(self, args: List[str]) -> None:
        """Runs mlx_lm's training CLI as a subprocess, captured rather than
        streamed raw -- "Iter N: ..." lines update a real (current/total)
        sub-progress bar via tam.status instead of scrolling the terminal, but
        the full captured output is dumped on a non-zero exit so a real
        failure is still fully diagnosable, not silently swallowed."""
        import subprocess

        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        lines: List[str] = []
        for line in process.stdout:
            lines.append(line)
            match = _ITER_RE.match(line.strip())
            if match:
                iteration, detail = int(match.group(1)), match.group(2)
                report(f"fine-tuning gen {self._generation}: {detail}", iteration, self._iters)
        process.wait()

        if process.returncode != 0:
            sys.stderr.write("".join(lines))
            raise subprocess.CalledProcessError(process.returncode, args)

    def get_state(self) -> dict:
        """Everything not already durable on disk under adapter_root -- the
        unflushed outcome buffer and where we are in the fine-tune cadence --
        so a crash between fine-tune passes doesn't lose partial progress."""
        return {
            "buffer": list(self._buffer),
            "days_since_fine_tune": self._days_since_fine_tune,
            "generation": self._generation,
            "current_adapter": str(self._current_adapter) if self._current_adapter else None,
        }

    def load_state(self, state: dict) -> None:
        self._buffer = list(state["buffer"])
        self._days_since_fine_tune = state["days_since_fine_tune"]
        self._generation = state["generation"]
        current_adapter = state["current_adapter"]
        self._current_adapter = Path(current_adapter) if current_adapter else None
        self._model = None  # force a reload so the next call picks up the restored adapter
