import json
import subprocess
import sys
from pathlib import Path

import pytest

from tam import status
from tam.strategy.mlx_lora_client import MLXLoRAClient


class _FakeTokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return "TEMPLATED:" + json.dumps(messages)


def _stub_ensure_loaded(monkeypatch):
    def fake_ensure_loaded(self):
        self._model = "fake-model"
        self._tokenizer = _FakeTokenizer()

    monkeypatch.setattr(MLXLoRAClient, "_ensure_loaded", fake_ensure_loaded)


def test_call_generates_via_templated_prompt(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path))
    _stub_ensure_loaded(monkeypatch)

    captured = {}

    def fake_generate(model, tokenizer, prompt, verbose=False, max_tokens=8):
        captured["prompt"] = prompt
        return "LONG"

    monkeypatch.setattr("mlx_lm.generate", fake_generate)

    result = client("what should I do today?")

    assert result == "LONG"
    assert "what should I do today?" in captured["prompt"]


def test_record_outcome_triggers_fine_tune_after_period(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=3)
    _stub_ensure_loaded(monkeypatch)

    calls = []

    def fake_run_training(self, args):
        calls.append(args)
        adapter_path = Path(args[args.index("--adapter-path") + 1])
        adapter_path.mkdir(parents=True, exist_ok=True)
        (adapter_path / "adapters.safetensors").write_bytes(b"fake")

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    client.record_outcome("prompt A", "long")
    client.record_outcome("prompt B", "short")
    assert len(calls) == 0  # not yet at the period

    client.record_outcome("prompt C", "long")
    assert len(calls) == 1  # period reached -> fine-tune fired
    assert client._buffer == []
    assert client._current_adapter is not None


def test_fine_tune_never_passes_mask_prompt_and_defaults_to_grad_checkpoint(tmp_path, monkeypatch):
    # --mask-prompt is deliberately never passed: mlx_lm computes its mask
    # offset by re-templating everything but the assistant turn, and for our
    # very short completions ("+42") that can consume the whole sequence,
    # producing 0 trained tokens and NaN loss (confirmed against mlx_lm's own
    # ChatDataset.process). --grad-checkpoint is on by default since fine-tuning
    # this model was observed using 25GB+ without it.
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1)
    _stub_ensure_loaded(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client.record_outcome("prompt", "long")

    assert "--mask-prompt" not in calls[0]
    assert "--grad-checkpoint" in calls[0]


def test_grad_checkpoint_can_be_disabled(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1, grad_checkpoint=False)
    _stub_ensure_loaded(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client.record_outcome("prompt", "long")

    assert "--grad-checkpoint" not in calls[0]


def test_fine_tune_writes_realized_outcome_as_the_training_target(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1, system_prompt="be disciplined")
    _stub_ensure_loaded(monkeypatch)

    written = {}

    def fake_run_training(self, args):
        data_dir = Path(args[args.index("--data") + 1])
        lines = (data_dir / "train.jsonl").read_text().splitlines()
        written["records"] = [json.loads(line) for line in lines]
        adapter_path = Path(args[args.index("--adapter-path") + 1])
        adapter_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    client.record_outcome("what happened today?", "short")

    [record] = written["records"]
    assert record["messages"][0] == {"role": "system", "content": "be disciplined"}
    assert record["messages"][1] == {"role": "user", "content": "what happened today?"}
    assert record["messages"][2] == {"role": "assistant", "content": "SHORT"}


def test_second_fine_tune_resumes_from_the_first_adapter(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1)
    _stub_ensure_loaded(monkeypatch)

    calls = []

    def fake_run_training(self, args):
        calls.append(args)
        adapter_path = Path(args[args.index("--adapter-path") + 1])
        adapter_path.mkdir(parents=True, exist_ok=True)
        (adapter_path / "adapters.safetensors").write_bytes(b"fake")

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    client.record_outcome("day 1", "long")
    client.record_outcome("day 2", "short")

    assert "--resume-adapter-file" not in calls[0]
    assert "--resume-adapter-file" in calls[1]
    resume_path = calls[1][calls[1].index("--resume-adapter-file") + 1]
    assert "gen_1" in resume_path


def test_fine_tune_with_empty_buffer_is_a_noop(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1)
    _stub_ensure_loaded(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client._fine_tune()  # buffer is empty -- nothing recorded yet

    assert calls == []


def test_construction_resumes_from_latest_generation_already_on_disk(tmp_path):
    for gen in (1, 2, 3):
        adapter_dir = tmp_path / f"gen_{gen}"
        adapter_dir.mkdir()
        (adapter_dir / "adapters.safetensors").write_bytes(b"fake")
    (tmp_path / "gen_4").mkdir()  # incomplete -- no adapters.safetensors -- must be skipped

    client = MLXLoRAClient(adapter_root=str(tmp_path))

    assert client._current_adapter == tmp_path / "gen_3"
    assert client._generation == 3


def test_run_training_reports_parsed_iter_progress(tmp_path):
    client = MLXLoRAClient(adapter_root=str(tmp_path), iters=100)
    calls = []
    status.set_reporter(lambda text, current, total: calls.append((text, current, total)))
    try:
        script = "print('Iter 1: Train loss 2.500'); print('Iter 2: Train loss 2.100')"
        client._run_training([sys.executable, "-c", script])
    finally:
        status.set_reporter(None)

    iter_calls = [c for c in calls if c[1] is not None]
    assert iter_calls == [
        ("fine-tuning gen 0: loss 2.500", 1, 100),
        ("fine-tuning gen 0: loss 2.100", 2, 100),
    ]


def test_run_training_raises_and_surfaces_output_on_nonzero_exit(tmp_path, capsys):
    client = MLXLoRAClient(adapter_root=str(tmp_path))
    script = "print('boom, something went wrong'); import sys; sys.exit(1)"

    with pytest.raises(subprocess.CalledProcessError):
        client._run_training([sys.executable, "-c", script])

    assert "boom, something went wrong" in capsys.readouterr().err
