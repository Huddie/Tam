from pathlib import Path

import pytest

from tam.discovery.auth import resolve_token, token_file_path


def test_token_file_path_is_under_home_config():
    assert token_file_path() == Path.home() / ".config" / "upload-discovery" / "token"


def test_resolve_token_explicit_argument_wins_over_everything(monkeypatch):
    monkeypatch.setenv("TAM_DISCOVERY_TOKEN", "from-env")

    assert resolve_token("explicit-token") == "explicit-token"


def test_resolve_token_env_var_wins_over_saved_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TAM_DISCOVERY_TOKEN", "from-env")
    token_path = tmp_path / "token"
    token_path.write_text("from-file")
    monkeypatch.setattr("tam.discovery.auth.token_file_path", lambda: token_path)

    assert resolve_token() == "from-env"


def test_resolve_token_falls_back_to_saved_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TAM_DISCOVERY_TOKEN", raising=False)
    token_path = tmp_path / "token"
    token_path.write_text("from-file\n")
    monkeypatch.setattr("tam.discovery.auth.token_file_path", lambda: token_path)

    assert resolve_token() == "from-file"


def test_resolve_token_raises_a_clear_error_when_nothing_is_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("TAM_DISCOVERY_TOKEN", raising=False)
    monkeypatch.setattr("tam.discovery.auth.token_file_path", lambda: tmp_path / "does-not-exist")

    with pytest.raises(RuntimeError, match="No Discovery publishing token found"):
        resolve_token()


def test_resolve_token_uses_the_colab_secret_when_running_in_colab(monkeypatch):
    import sys
    import types

    monkeypatch.delenv("TAM_DISCOVERY_TOKEN", raising=False)
    fake_colab = types.ModuleType("google.colab")
    fake_colab.userdata = types.SimpleNamespace(get=lambda name: "from-colab" if name == "TAM_DISCOVERY_TOKEN" else None)
    monkeypatch.setitem(sys.modules, "google.colab", fake_colab)

    assert resolve_token() == "from-colab"


def test_resolve_token_falls_through_to_the_file_when_the_colab_lookup_raises(monkeypatch, tmp_path):
    # google.colab.userdata raises its OWN SecretNotFoundError/NotebookAccessError
    # when the secret isn't configured that way -- resolve_token must treat any
    # exception here as "not this source" and keep trying the rest, not propagate.
    import sys
    import types

    monkeypatch.delenv("TAM_DISCOVERY_TOKEN", raising=False)

    def _raise(name):
        raise RuntimeError("SecretNotFoundError")

    fake_colab = types.ModuleType("google.colab")
    fake_colab.userdata = types.SimpleNamespace(get=_raise)
    monkeypatch.setitem(sys.modules, "google.colab", fake_colab)

    token_path = tmp_path / "token"
    token_path.write_text("from-file")
    monkeypatch.setattr("tam.discovery.auth.token_file_path", lambda: token_path)

    assert resolve_token() == "from-file"


def test_resolve_token_ignores_colab_module_when_not_actually_in_colab(monkeypatch, tmp_path):
    import sys

    monkeypatch.delenv("TAM_DISCOVERY_TOKEN", raising=False)
    monkeypatch.delitem(sys.modules, "google.colab", raising=False)
    token_path = tmp_path / "token"
    token_path.write_text("from-file")
    monkeypatch.setattr("tam.discovery.auth.token_file_path", lambda: token_path)

    assert resolve_token() == "from-file"
