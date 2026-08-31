"""Model: the pluggable architecture layer for tam.ml's research harness --
fit/predict/save/load, `Registry(Model, ...)`-backed exactly like `Factor`/
`Strategy` elsewhere in this codebase. Concrete implementations are built on
`skorch` (a sklearn-compatible wrapper around a plain `torch.nn.Module`)
rather than a hand-rolled training loop -- see this session's build-vs-buy
analysis: skorch already gives early stopping, checkpointing, and a uniform
fit/predict contract, so "the meat" of a new architecture is writing ONE
`nn.Module` class, nothing else.

`torch`/`skorch` are imported lazily inside these functions/methods only,
not at module top -- `import tam.ml.model` (and anything that imports it,
including `tam.ml.experiment`) works fine without either installed; only
actually building/training a model needs them (see the `ml` extra in
pyproject.toml).
"""

from __future__ import annotations

import pickle
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from ..registry import Registry


class Model(ABC):
    """One trained thing: standardized features in, a confidence-signed
    score out. Every architecture must implement exactly this -- dataset
    construction, splitting, and analysis (`tam.ml.dataset`/`analysis`) are
    written once against this contract and never touch model internals."""

    @abstractmethod
    def fit(self, x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray, y_val: np.ndarray) -> None: ...

    @abstractmethod
    def predict(self, x: np.ndarray) -> np.ndarray: ...

    @abstractmethod
    def save(self, path: str) -> None: ...

    @abstractmethod
    def load(self, path: str) -> None: ...


class SkorchModel(Model):
    """A `Model` built on `skorch.NeuralNetRegressor` -- standardization
    (`sklearn.preprocessing.StandardScaler`, fit on train only, same
    no-leakage rule the original Colab guide already established), early
    stopping (`skorch.callbacks.EarlyStopping`, `load_best=True`), and
    checkpointing (skorch's own `save_params`/`load_params`) all come from
    skorch/sklearn, not hand-rolled here.

    The caller's own pre-split, time-ordered validation set is wired in via
    `skorch.helper.predefined_split` -- **not** skorch's default internal
    `train_split` (which re-splits `x_train` itself, randomly, and would
    silently reintroduce the exact leakage `tam.ml.dataset.time_split()`
    exists to prevent).

    Subclasses implement `_build_module(n_features) -> nn.Module` -- that's
    the entire "meat" a new architecture needs to supply; everything else
    below is shared."""

    def __init__(
        self,
        max_epochs: int = 300,
        patience: int = 15,
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        device: str = "cpu",
    ):
        self._max_epochs = max_epochs
        self._patience = patience
        self._lr = lr
        self._weight_decay = weight_decay
        self._device = device
        self._net = None
        self._scaler = None
        self._n_features = None

    @abstractmethod
    def _build_module(self, n_features: int) -> "torch.nn.Module": ...  # noqa: F821 -- torch is a lazy/optional import

    def _build_net(self, n_features: int, valid_ds):
        import torch
        from skorch import NeuralNetRegressor
        from skorch.callbacks import EarlyStopping
        from skorch.helper import predefined_split

        module = self._build_module(n_features)
        return NeuralNetRegressor(
            module=module,
            max_epochs=self._max_epochs,
            optimizer=torch.optim.Adam,
            lr=self._lr,
            optimizer__weight_decay=self._weight_decay,
            train_split=predefined_split(valid_ds),
            callbacks=[EarlyStopping(monitor="valid_loss", patience=self._patience, load_best=True)],
            device=self._device,
            verbose=0,
        )

    @staticmethod
    def _as_float32_columns(y: np.ndarray) -> np.ndarray:
        # skorch/torch's MSELoss wants (n, 1), not a bare (n,) vector.
        y = np.asarray(y, dtype="float32")
        return y.reshape(-1, 1) if y.ndim == 1 else y

    def fit(self, x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray, y_val: np.ndarray) -> None:
        from sklearn.preprocessing import StandardScaler
        from skorch.dataset import Dataset

        self._scaler = StandardScaler().fit(x_train)
        x_train_scaled = self._scaler.transform(x_train).astype("float32")
        x_val_scaled = self._scaler.transform(x_val).astype("float32")

        valid_ds = Dataset(x_val_scaled, self._as_float32_columns(y_val))
        self._n_features = x_train.shape[1]
        self._net = self._build_net(self._n_features, valid_ds)
        self._net.fit(x_train_scaled, self._as_float32_columns(y_train))

    def predict(self, x: np.ndarray) -> np.ndarray:
        x_scaled = self._scaler.transform(x).astype("float32")
        return np.asarray(self._net.predict(x_scaled)).reshape(-1)

    def save(self, path: str) -> None:
        """Weights go to `path` via skorch's own `save_params` (a plain
        state_dict file, same "a path, not a blob" convention
        `tam/strategy/mlx_lora_client.py` uses); every OTHER instance
        attribute (the scaler, `n_features`, and whatever architecture
        kwargs a concrete subclass stores on itself -- e.g. `MLPModel`'s
        `_hidden`) goes to a small sidecar `<path>.meta.pkl` next to it,
        generically (`vars(self)` minus `_net`) rather than a hand-picked
        subset -- a subclass with its OWN extra constructor kwargs gets
        those saved/restored for free, with no override needed here."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._net.save_params(f_params=str(target))
        state = {key: value for key, value in vars(self).items() if key != "_net"}
        with open(f"{target}.meta.pkl", "wb") as handle:
            pickle.dump(state, handle)

    def load(self, path: str) -> None:
        from skorch.dataset import Dataset

        with open(f"{path}.meta.pkl", "rb") as handle:
            state = pickle.load(handle)
        self.__dict__.update(
            state
        )  # restores _scaler, _n_features, and any subclass-specific attrs (e.g. MLPModel's _hidden)

        # A throwaway 2-row valid_ds, just to give skorch a concrete shape to
        # initialize the net's layers against -- load_params() below
        # immediately overwrites every learned weight anyway.
        dummy_x = np.zeros((2, self._n_features), dtype="float32")
        dummy_y = np.zeros((2, 1), dtype="float32")
        self._net = self._build_net(self._n_features, Dataset(dummy_x, dummy_y))
        self._net.initialize()
        self._net.load_params(f_params=str(path))


@Registry.register(Model, "mlp")
class MLPModel(SkorchModel):
    """Small 2-3 layer MLP regressor over engineered features -- the
    original Colab guide's exact architecture, now the built-in default.
    `hidden=` is the only architecture knob."""

    def __init__(self, hidden: int = 32, **kwargs):
        super().__init__(**kwargs)
        self._hidden = hidden

    def _build_module(self, n_features: int) -> "torch.nn.Module":  # noqa: F821
        import torch.nn as nn

        hidden = self._hidden
        return nn.Sequential(
            nn.Linear(n_features, hidden),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, 1),
        )
