"""Tests for tam.ml.model -- Model/SkorchModel/MLPModel: fit/predict on a
learnable synthetic relationship, save/load round-trip, and the two things
that would silently reintroduce leakage or break checkpointing if wrong:
predefined_split actually using the caller's own val set (not skorch's
internal random split), and standardization fit on train only.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("skorch")

from tam.ml.model import Model  # noqa: E402
from tam.registry import Registry  # noqa: E402

requires_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None or importlib.util.find_spec("skorch") is None,
    reason="torch/skorch are optional (tam-quant[ml])",
)


def _linear_dataset(rng, n, n_features=4, noise=0.05):
    x = rng.normal(0, 1, (n, n_features)).astype("float32")
    y = (2.0 * x[:, 0] - 1.0 * x[:, 1] + rng.normal(0, noise, n)).astype("float32")
    return x, y


@requires_torch
def test_mlp_fits_and_predicts_a_learnable_linear_relationship():
    rng = np.random.default_rng(0)
    x_train, y_train = _linear_dataset(rng, 200)
    x_val, y_val = _linear_dataset(rng, 50)
    x_test, y_test = _linear_dataset(rng, 50, noise=0.0)

    model = Registry.create(Model, "mlp", hidden=16, max_epochs=150, patience=15)
    model.fit(x_train, y_train, x_val, y_val)
    preds = model.predict(x_test)

    assert preds.shape == (50,)
    assert np.corrcoef(preds, y_test)[0, 1] > 0.7


@requires_torch
def test_save_load_round_trip_restores_a_non_default_architecture_kwarg(tmp_path):
    # Regression test: load() must rebuild the SAME shape the model was
    # trained with, not fall back to Registry.create(Model, "mlp")'s
    # constructor defaults -- a bare `Registry.create(Model, "mlp")` before
    # load() has hidden=32 (the default); if load() didn't restore the
    # ACTUAL trained hidden=8, rebuilding the net for load_params() would
    # use the wrong shape and either fail outright or silently predict
    # from mismatched/randomly-reinitialized weights.
    rng = np.random.default_rng(4)
    x_train, y_train = _linear_dataset(rng, 150)
    x_val, y_val = _linear_dataset(rng, 40)
    x_test, _ = _linear_dataset(rng, 20)

    model = Registry.create(Model, "mlp", hidden=8, max_epochs=40, patience=10)
    model.fit(x_train, y_train, x_val, y_val)
    before = model.predict(x_test)

    checkpoint_path = tmp_path / "model.pt"
    model.save(str(checkpoint_path))

    reloaded = Registry.create(Model, "mlp")  # deliberately NOT told hidden=8
    reloaded.load(str(checkpoint_path))

    assert reloaded._hidden == 8
    after = reloaded.predict(x_test)
    assert np.allclose(before, after, atol=1e-5)


@requires_torch
def test_save_load_round_trip_predicts_identically(tmp_path):
    rng = np.random.default_rng(1)
    x_train, y_train = _linear_dataset(rng, 150)
    x_val, y_val = _linear_dataset(rng, 40)
    x_test, _ = _linear_dataset(rng, 20)

    model = Registry.create(Model, "mlp", hidden=8, max_epochs=60, patience=10)
    model.fit(x_train, y_train, x_val, y_val)
    before = model.predict(x_test)

    checkpoint_path = tmp_path / "model.pt"
    model.save(str(checkpoint_path))

    reloaded = Registry.create(Model, "mlp", hidden=8)
    reloaded.load(str(checkpoint_path))
    after = reloaded.predict(x_test)

    assert np.allclose(before, after, atol=1e-5)


@requires_torch
def test_fit_uses_the_callers_own_val_set_not_an_internal_random_split():
    # Train is pure noise (no learnable relationship at all); val is scaled
    # 1000x larger. If skorch silently re-split x_train internally instead
    # of using our val set, valid_loss would stay small (same scale as
    # train) instead of reflecting our actual, wildly-different-scale val set.
    rng = np.random.default_rng(2)
    x_train = rng.normal(0, 1, (100, 3)).astype("float32")
    y_train = rng.normal(0, 1, 100).astype("float32")
    x_val = (rng.normal(0, 1, (20, 3)) * 1000).astype("float32")
    y_val = (rng.normal(0, 1, 20) * 1000).astype("float32")

    model = Registry.create(Model, "mlp", hidden=8, max_epochs=5, patience=100)
    model.fit(x_train, y_train, x_val, y_val)

    valid_losses = [epoch["valid_loss"] for epoch in model._net.history]
    assert all(loss > 1000 for loss in valid_losses)


@requires_torch
def test_standardization_is_fit_on_train_only():
    rng = np.random.default_rng(3)
    x_train, y_train = _linear_dataset(rng, 100)
    x_val, y_val = _linear_dataset(rng, 20)

    model = Registry.create(Model, "mlp", hidden=8, max_epochs=5, patience=100)
    model.fit(x_train, y_train, x_val, y_val)

    assert np.allclose(model._scaler.mean_, x_train.mean(axis=0), atol=1e-5)
    assert np.allclose(model._scaler.scale_, x_train.std(axis=0), atol=1e-5)
