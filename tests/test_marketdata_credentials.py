import json

import pytest

from tam.marketdata import credentials as creds_module
from tam.marketdata.credentials import R2Credentials, resolve_r2_credentials, save_r2_credentials

_ENV_VARS = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # resolve_r2_credentials() now also checks a .env file, found by
    # walking UP from the current directory (see credentials.py's own
    # _from_dotenv) -- without this, every test in this file would run
    # from the repo root and silently pick up ITS real .env file's real
    # R2_* values. tmp_path is always outside the repo, so walking up
    # from there finds nothing, same as before this feature existed.
    monkeypatch.chdir(tmp_path)


def test_resolve_from_explicit_kwargs():
    result = resolve_r2_credentials(account_id="acct", access_key_id="key", secret_access_key="secret", bucket="bucket")
    assert result == R2Credentials(account_id="acct", access_key_id="key", secret_access_key="secret", bucket="bucket")


def test_resolve_from_env_vars(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET", "bucket")

    result = resolve_r2_credentials()

    assert result == R2Credentials(account_id="acct", access_key_id="key", secret_access_key="secret", bucket="bucket")


def test_explicit_kwarg_wins_over_env_var(monkeypatch):
    monkeypatch.setenv("R2_BUCKET", "env-bucket")
    result = resolve_r2_credentials(account_id="a", access_key_id="k", secret_access_key="s", bucket="explicit-bucket")
    assert result.bucket == "explicit-bucket"


def test_missing_fields_raise_actionable_error_naming_every_missing_field():
    with pytest.raises(RuntimeError) as exc_info:
        resolve_r2_credentials(account_id="a", access_key_id="k")

    message = str(exc_info.value)
    first_line = message.splitlines()[0]
    assert "secret_access_key" in first_line
    assert "bucket" in first_line
    assert "account_id" not in first_line
    assert "R2_SECRET_ACCESS_KEY" in message
    assert "R2_BUCKET" in message


def test_endpoint_derived_from_account_id():
    result = R2Credentials(account_id="myaccount123", access_key_id="k", secret_access_key="s", bucket="b")
    assert result.endpoint == "https://myaccount123.r2.cloudflarestorage.com"


def test_save_and_resolve_from_saved_file(tmp_path, monkeypatch):
    saved_path = tmp_path / "r2_credentials.json"
    monkeypatch.setattr(creds_module, "credentials_file_path", lambda: saved_path)

    original = R2Credentials(account_id="acct", access_key_id="key", secret_access_key="secret", bucket="bucket")
    returned_path = save_r2_credentials(original)

    assert returned_path == saved_path
    assert json.loads(saved_path.read_text())["bucket"] == "bucket"

    resolved = resolve_r2_credentials()
    assert resolved == original


def test_colab_secret_used_only_when_colab_module_present(monkeypatch):
    """_from_colab must not blow up (or find anything) when google.colab
    isn't actually importable -- the common case for every non-Colab
    environment, including CI."""
    assert "google.colab" not in __import__("sys").modules
    with pytest.raises(RuntimeError):
        resolve_r2_credentials()


def test_resolve_from_a_dotenv_file_found_by_walking_up_from_a_subdirectory(tmp_path, monkeypatch):
    nested = tmp_path / "nested" / "deeper"
    nested.mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "R2_ACCOUNT_ID=acct\nR2_ACCESS_KEY_ID=key\nR2_SECRET_ACCESS_KEY=secret\nR2_BUCKET=bucket\n"
    )
    monkeypatch.chdir(nested)

    result = resolve_r2_credentials()

    assert result == R2Credentials(account_id="acct", access_key_id="key", secret_access_key="secret", bucket="bucket")


def test_a_real_env_var_wins_over_the_dotenv_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "R2_ACCOUNT_ID=from-dotenv\nR2_ACCESS_KEY_ID=key\nR2_SECRET_ACCESS_KEY=secret\nR2_BUCKET=bucket\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("R2_ACCOUNT_ID", "from-env-var")

    result = resolve_r2_credentials()

    assert result.account_id == "from-env-var"
