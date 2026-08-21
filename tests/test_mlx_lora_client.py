import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tam import status
from tam.strategy.mlx_lora_client import MLXLoRAClient


class _FakeTokenizer:
    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=True, return_dict=False):
        if not tokenize:
            return "TEMPLATED:" + json.dumps(messages)
        return list(range(len(json.dumps(messages))))  # fake token count, plenty under any test max_seq_length


def _stub_ensure_loaded(monkeypatch):
    def fake_ensure_loaded(self):
        self._model = "fake-model"
        self._tokenizer = _FakeTokenizer()

    monkeypatch.setattr(MLXLoRAClient, "_ensure_loaded", fake_ensure_loaded)


def _stub_adapter_healthy(monkeypatch, healthy=True):
    monkeypatch.setattr(MLXLoRAClient, "_adapter_is_healthy", lambda self, adapter_path: healthy)


def _config_from_args(args: list) -> dict:
    """The training subprocess is now invoked as `mlx_lm lora -c <path>`, with
    every mlx_lm-specific knob living in that YAML file rather than as argv
    flags -- tests read the file back to assert on what was actually asked
    for. See mlx_lora_client.py's _mlx_lm_config."""
    assert args[-2] == "-c"
    return yaml.safe_load(Path(args[-1]).read_text())


def _fake_run_training_creating_adapter(self, args):
    config = _config_from_args(args)
    adapter_path = Path(config["adapter_path"])
    adapter_path.mkdir(parents=True, exist_ok=True)
    (adapter_path / "adapters.safetensors").write_bytes(b"fake")


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
    _stub_adapter_healthy(monkeypatch)

    calls = []

    def fake_run_training(self, args):
        calls.append(args)
        _fake_run_training_creating_adapter(self, args)

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    client.record_outcome("prompt A", "long")
    client.record_outcome("prompt B", "short")
    assert len(calls) == 0  # not yet at the period

    client.record_outcome("prompt C", "long")
    assert len(calls) == 1  # period reached -> fine-tune fired
    assert client._history == [("prompt A", "LONG"), ("prompt B", "SHORT"), ("prompt C", "LONG")]  # kept, not cleared
    assert client._current_adapter is not None


def test_fine_tune_passes_mask_prompt_and_defaults_to_grad_checkpoint(tmp_path, monkeypatch):
    # mask_prompt restricts the loss to the assistant's completion tokens,
    # not the much longer prompt. grad_checkpoint is on by default since
    # fine-tuning this model was observed using 25GB+ without it.
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1)
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client.record_outcome("prompt", "long")

    config = _config_from_args(calls[0])
    assert config["mask_prompt"] is True
    assert config["grad_checkpoint"] is True
    assert config["max_seq_length"] == 4096


def test_max_seq_length_is_configurable(tmp_path, monkeypatch):
    # mlx_lm silently truncates every training sequence to max_seq_length
    # (default 2048) -- too low for this strategy's long signal-history
    # prompts, which truncates away the assistant's completion entirely and
    # trains on 0 real tokens every pass. Must be overridable per config.
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1, max_seq_length=12288)
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client.record_outcome("prompt", "long")

    assert _config_from_args(calls[0])["max_seq_length"] == 12288


def test_batch_size_is_unset_by_default_but_configurable(tmp_path, monkeypatch):
    # A long max_seq_length can OOM at mlx_lm's own default batch size (4)
    # even with grad-checkpoint on -- confirmed empirically, not just in
    # theory. Left unset by default so callers with short prompts keep
    # mlx_lm's own default, but must be lowerable per config.
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1)
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)
    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))
    client.record_outcome("prompt", "long")
    assert "batch_size" not in _config_from_args(calls[0])

    client2 = MLXLoRAClient(adapter_root=str(tmp_path / "b"), fine_tune_every_n_days=1, batch_size=1)
    calls2 = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls2.append(args))
    client2.record_outcome("prompt", "long")
    assert _config_from_args(calls2[0])["batch_size"] == 1


def test_grad_checkpoint_can_be_disabled(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1, grad_checkpoint=False)
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client.record_outcome("prompt", "long")

    assert _config_from_args(calls[0])["grad_checkpoint"] is False


def test_lora_regularization_and_optimizer_are_configurable(tmp_path, monkeypatch):
    # dropout/weight_decay both default to nonzero -- unlike mlx_lm's own
    # zero-regularization defaults -- specifically to fight the mode collapse
    # that cumulative, unregularized fine-tuning was observed causing (see
    # module docstring). Must still be overridable per config.
    client = MLXLoRAClient(
        adapter_root=str(tmp_path),
        fine_tune_every_n_days=1,
        lora_rank=16,
        lora_dropout=0.1,
        lora_scale=10.0,
        optimizer="adamw",
        weight_decay=0.05,
    )
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client.record_outcome("prompt", "long")

    config = _config_from_args(calls[0])
    assert config["lora_parameters"] == {"rank": 16, "dropout": 0.1, "scale": 10.0}
    assert config["optimizer"] == "adamw"
    assert config["optimizer_config"] == {"adamw": {"weight_decay": 0.05}}


def test_weight_decay_is_omitted_for_a_non_adamw_optimizer(tmp_path, monkeypatch):
    # mlx's plain Adam has no weight_decay parameter -- passing it would be a
    # straight constructor error in mlx.optimizers, not a no-op.
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1, optimizer="adam")
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client.record_outcome("prompt", "long")

    assert "optimizer_config" not in _config_from_args(calls[0])


def test_extra_mlx_config_overrides_named_settings(tmp_path, monkeypatch):
    # Escape hatch for any mlx_lm.lora YAML key that doesn't have its own
    # named constructor parameter -- deep-merged last, so it can override
    # (or add to) a nested key like lora_parameters without repeating the
    # rest of it.
    client = MLXLoRAClient(
        adapter_root=str(tmp_path),
        fine_tune_every_n_days=1,
        extra_mlx_config={"lora_parameters": {"rank": 32}, "seed": 7},
    )
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client.record_outcome("prompt", "long")

    config = _config_from_args(calls[0])
    assert config["lora_parameters"]["rank"] == 32
    assert config["lora_parameters"]["dropout"] == 0.05  # untouched sibling key survives the merge
    assert config["seed"] == 7


def test_fine_tune_writes_realized_outcome_as_the_training_target(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1, system_prompt="be disciplined")
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    written = {}

    def fake_run_training(self, args):
        config = _config_from_args(args)
        data_dir = Path(config["data"])
        lines = (data_dir / "train.jsonl").read_text().splitlines()
        written["records"] = [json.loads(line) for line in lines]
        Path(config["adapter_path"]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    client.record_outcome("what happened today?", "short")

    [record] = written["records"]
    assert record["messages"][0] == {"role": "system", "content": "be disciplined"}
    assert record["messages"][1] == {"role": "user", "content": "what happened today?"}
    assert record["messages"][2] == {"role": "assistant", "content": "SHORT"}


def test_completion_truncated_to_zero_tokens_raises_before_training_starts(tmp_path, monkeypatch):
    # Regression guard for the "measured ~10k tokens once, by hand" failure
    # mode -- a max_seq_length too small for an example's real prompt length
    # must be caught loudly, before ever starting the training subprocess,
    # not left to surface as a silent "Trained Tokens 0" / NaN loss.
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1, max_seq_length=1)
    _stub_ensure_loaded(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    with pytest.raises(ValueError, match="max_seq_length"):
        client.record_outcome("prompt", "long")

    assert calls == []  # never reached the training subprocess


def test_val_split_writes_a_holdout_set_and_enables_val_batches(tmp_path, monkeypatch):
    # Below _MIN_EXAMPLES_FOR_VALIDATION_SPLIT, holding anything out isn't
    # worth shrinking the (already small) training set for -- val_batches
    # stays 0 and no valid.jsonl is written, matching the old no-validation
    # behavior for tiny buffers.
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1, val_split=0.2)
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    written = {}

    def fake_run_training(self, args):
        config = _config_from_args(args)
        data_dir = Path(config["data"])
        written["val_batches"] = config["val_batches"]
        written["has_valid_file"] = (data_dir / "valid.jsonl").exists()
        Path(config["adapter_path"]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    client.record_outcome("prompt", "long")

    assert written["val_batches"] == 0
    assert written["has_valid_file"] is False


def test_val_split_holds_out_a_fraction_once_buffer_is_large_enough(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=10, val_split=0.2)
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    written = {}

    def fake_run_training(self, args):
        config = _config_from_args(args)
        data_dir = Path(config["data"])
        written["val_batches"] = config["val_batches"]
        train_count = len((data_dir / "train.jsonl").read_text().splitlines())
        valid_count = len((data_dir / "valid.jsonl").read_text().splitlines())
        written["train_count"] = train_count
        written["valid_count"] = valid_count
        Path(config["adapter_path"]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    for i in range(10):
        client.record_outcome(f"prompt {i}", "long")

    assert written["val_batches"] == -1
    assert written["valid_count"] == 2  # int(10 * 0.2)
    assert written["train_count"] == 8


def test_max_val_examples_caps_the_holdout_regardless_of_val_split(tmp_path, monkeypatch):
    # A straight val_split fraction of an ever-growing history means both the
    # held-out set and its per-round evaluation cost grow without bound over
    # a long backtest -- max_val_examples caps it so wall-clock cost per
    # round stays roughly constant instead of getting slower every round.
    client = MLXLoRAClient(
        adapter_root=str(tmp_path), fine_tune_every_n_days=20, val_split=0.5, max_val_examples=3
    )
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    written = {}

    def fake_run_training(self, args):
        config = _config_from_args(args)
        data_dir = Path(config["data"])
        written["valid_count"] = len((data_dir / "valid.jsonl").read_text().splitlines())
        written["train_count"] = len((data_dir / "train.jsonl").read_text().splitlines())
        Path(config["adapter_path"]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    for i in range(20):
        client.record_outcome(f"prompt {i}", "long")

    assert written["valid_count"] == 3  # capped, not int(20 * 0.5) == 10
    assert written["train_count"] == 17


def test_second_fine_tune_trains_fresh_on_the_full_accumulated_history(tmp_path, monkeypatch):
    # No resume_adapter_file, ever -- every round trains a fresh adapter from
    # the base model, but each round's training data is the FULL history
    # accumulated so far, not just what's new since the last round (see
    # module docstring: this replaced cumulative resuming + a per-round
    # buffer, which caused both regime amnesia and unbounded collapse risk).
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1)
    _stub_ensure_loaded(monkeypatch)
    _stub_adapter_healthy(monkeypatch)

    calls = []
    written_train_counts = []

    def fake_run_training(self, args):
        calls.append(args)
        config = _config_from_args(args)
        data_dir = Path(config["data"])
        written_train_counts.append(len((data_dir / "train.jsonl").read_text().splitlines()))
        _fake_run_training_creating_adapter(self, args)

    monkeypatch.setattr(MLXLoRAClient, "_run_training", fake_run_training)

    client.record_outcome("day 1", "long")
    client.record_outcome("day 2", "short")

    assert "resume_adapter_file" not in _config_from_args(calls[0])
    assert "resume_adapter_file" not in _config_from_args(calls[1])
    assert written_train_counts == [1, 2]  # round 2 retrains on both days, not just the new one


def test_fine_tune_with_empty_buffer_is_a_noop(tmp_path, monkeypatch):
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1)
    _stub_ensure_loaded(monkeypatch)

    calls = []
    monkeypatch.setattr(MLXLoRAClient, "_run_training", lambda self, args: calls.append(args))

    client._fine_tune()  # buffer is empty -- nothing recorded yet

    assert calls == []


def test_degenerate_adapter_is_discarded_and_prior_adapter_kept(tmp_path, monkeypatch):
    # Regression test: unregularized, cumulative LoRA fine-tuning was observed
    # driving the model into repetition/mode collapse (every inference call
    # returning a fixed non-numeric string like "!!!!!!!!!!!!!!!!") -- and
    # because the collapsed adapter became _current_adapter, every later
    # fine-tune resumed from it, making the collapse permanent for the rest
    # of the backtest. A validation gate must catch this and keep serving the
    # last known-good adapter instead.
    client = MLXLoRAClient(adapter_root=str(tmp_path), fine_tune_every_n_days=1)
    _stub_ensure_loaded(monkeypatch)

    monkeypatch.setattr(MLXLoRAClient, "_run_training", _fake_run_training_creating_adapter)
    _stub_adapter_healthy(monkeypatch, healthy=True)

    client.record_outcome("day 1", "long")
    good_adapter = client._current_adapter
    assert good_adapter is not None
    assert client._generation == 1

    _stub_adapter_healthy(monkeypatch, healthy=False)

    client.record_outcome("day 2", "short")

    assert client._current_adapter == good_adapter  # rolled back, not the new gen_2
    assert client._generation == 1  # the failed generation number isn't reused/kept
    assert not (tmp_path / "gen_2").exists()  # discarded from disk, not left behind

    _stub_adapter_healthy(monkeypatch, healthy=True)
    client.record_outcome("day 3", "long")

    # the next successful fine-tune resumes from the last known-good adapter,
    # not from the discarded one
    assert client._current_adapter == tmp_path / "gen_2"


def test_diverged_validation_loss_fails_health_check_without_running_inference(tmp_path, monkeypatch):
    # A validation-loss blowup is a diagnosable-in-advance sign of the same
    # collapse the inference-sample check screens for -- catch it without
    # even needing to load the model for inference.
    client = MLXLoRAClient(adapter_root=str(tmp_path))
    client._last_val_losses = [(1, 1.0), (50, 5.0)]  # tripled -- diverging, not converging

    inference_called = []
    monkeypatch.setattr(
        "mlx_lm.utils.load", lambda *a, **k: inference_called.append(True) or (None, None)
    )

    assert client._adapter_is_healthy(tmp_path / "gen_1") is False
    assert inference_called == []


def test_construction_resumes_from_latest_generation_already_on_disk(tmp_path):
    for gen in (1, 2, 3):
        adapter_dir = tmp_path / f"gen_{gen}"
        adapter_dir.mkdir()
        (adapter_dir / "adapters.safetensors").write_bytes(b"fake")
    (tmp_path / "gen_4").mkdir()  # incomplete -- no adapters.safetensors -- must be skipped

    client = MLXLoRAClient(adapter_root=str(tmp_path))

    assert client._current_adapter == tmp_path / "gen_3"
    assert client._generation == 3


def test_load_state_accepts_a_pre_full_history_checkpoints_buffer_key(tmp_path):
    # Checkpoints written before history stopped being cleared after each
    # round used "buffer" for the same idea, just scoped to one round --
    # an in-progress backtest resuming from one of those must not break.
    client = MLXLoRAClient(adapter_root=str(tmp_path))
    old_style_state = {
        "buffer": [("old prompt", "LONG")],
        "days_since_fine_tune": 3,
        "generation": 0,
        "current_adapter": None,
    }

    client.load_state(old_style_state)

    assert client._history == [("old prompt", "LONG")]


def test_get_state_round_trips_through_load_state_via_the_history_key(tmp_path):
    client = MLXLoRAClient(adapter_root=str(tmp_path))
    client._history = [("p1", "LONG"), ("p2", "SHORT")]
    client._days_since_fine_tune = 2

    restored = MLXLoRAClient(adapter_root=str(tmp_path / "b"))
    restored.load_state(client.get_state())

    assert restored._history == client._history
    assert restored._days_since_fine_tune == client._days_since_fine_tune


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


def test_run_training_tracks_val_loss_lines_separately_from_train_loss(tmp_path):
    client = MLXLoRAClient(adapter_root=str(tmp_path), iters=100)
    calls = []
    status.set_reporter(lambda text, current, total: calls.append((text, current, total)))
    try:
        script = "print('Iter 1: Val loss 1.000, Val took 0.010s'); print('Iter 50: Val loss 3.000, Val took 0.010s')"
        client._run_training([sys.executable, "-c", script])
    finally:
        status.set_reporter(None)

    assert client._last_val_losses == [(1, 1.0), (50, 3.0)]
    iter_calls = [c for c in calls if c[1] is not None]
    assert iter_calls == [
        ("fine-tuning gen 0: val loss 1.000", 1, 100),
        ("fine-tuning gen 0: val loss 3.000", 50, 100),
    ]


def test_run_training_raises_and_surfaces_output_on_nonzero_exit(tmp_path, capsys):
    client = MLXLoRAClient(adapter_root=str(tmp_path))
    script = "print('boom, something went wrong'); import sys; sys.exit(1)"

    with pytest.raises(subprocess.CalledProcessError):
        client._run_training([sys.executable, "-c", script])

    assert "boom, something went wrong" in capsys.readouterr().err
