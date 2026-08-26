import importlib

import pytest

from tam.discovery.upload import UploadResult, _read_html, upload

# tam/discovery/__init__.py does `from .upload import upload`, which rebinds
# the `upload` name on the `tam.discovery` package to the FUNCTION -- so a
# string path like "tam.discovery.upload.DiscoveryClient" resolves via that
# shadowed attribute, not the submodule. Importing the real submodule object
# directly (bypassing the package's own attribute) is what monkeypatch needs
# to target below.
_upload_module = importlib.import_module("tam.discovery.upload")


class _FakeFigure:
    def __init__(self):
        self.to_html_calls = []

    def to_html(self, **kwargs):
        self.to_html_calls.append(kwargs)
        return "<html>fig</html>"


def test_read_html_serializes_a_plotly_like_figure_via_cdn_full_html():
    fig = _FakeFigure()

    assert _read_html(fig) == "<html>fig</html>"
    assert fig.to_html_calls == [{"full_html": True, "include_plotlyjs": "cdn"}]


def test_read_html_reads_an_existing_file(tmp_path):
    path = tmp_path / "x.html"
    path.write_text("<p>hi</p>")

    assert _read_html(path) == "<p>hi</p>"


def test_read_html_rejects_a_directory(tmp_path):
    with pytest.raises(ValueError, match="not a file"):
        _read_html(tmp_path)


class _FakeClient:
    """Records every call upload() makes, in order, so tests can assert the
    two-phase create -> PUT -> finalize flow without any real HTTP."""

    def __init__(self, token, api_url=None, timeout=30.0):
        self.token = token
        self.api_url = api_url
        self.calls = []

    def create_discovery(self, *, title, type, name):
        self.calls.append(("create_discovery", {"title": title, "type": type, "name": name}))
        return {"discovery_id": "disc-1", "type": type}

    def create_version(self, discovery_id, **fields):
        self.calls.append(("create_version", discovery_id, fields))
        return {"version_id": "ver-1", "upload_url": "https://r2.example/put", "upload_headers": {"x": "y"}}

    def upload_artifact(self, upload_url, upload_headers, content):
        self.calls.append(("upload_artifact", upload_url, upload_headers, content))

    def finalize_version(self, discovery_id, version_id, *, size_bytes):
        self.calls.append(("finalize_version", discovery_id, version_id, size_bytes))
        return {"url": "https://discovery.example.com/d/abc", "id": version_id, "version": 1, "title": "Fake Title"}


@pytest.fixture
def fake_client(monkeypatch):
    holder = {}

    def factory(token, api_url=None, timeout=30.0):
        client = _FakeClient(token, api_url, timeout)
        holder["client"] = client
        return client

    monkeypatch.setattr(_upload_module, "DiscoveryClient", factory)
    monkeypatch.setattr(_upload_module, "resolve_token", lambda explicit: explicit or "tamdisc_test")
    monkeypatch.setattr(_upload_module, "capture_git_info", lambda: {})
    return holder


def test_upload_happy_path_with_a_non_default_type(tmp_path, fake_client):
    html_path = tmp_path / "report.html"
    html_path.write_text("<html>hi</html>")

    result = upload(html_path, title="Q3 Report", type="report", tags=["q3", "finance"])

    assert isinstance(result, UploadResult)
    assert result.url == "https://discovery.example.com/d/abc"
    assert result.type == "report"
    assert result.version == 1

    client = fake_client["client"]
    assert [call[0] for call in client.calls] == ["create_discovery", "create_version", "upload_artifact", "finalize_version"]

    _, create_discovery_body = client.calls[0]
    assert create_discovery_body == {"title": "Q3 Report", "type": "report", "name": None}

    _, discovery_id, version_fields = client.calls[1]
    assert discovery_id == "disc-1"
    assert version_fields["tags"] == ["q3", "finance"]

    _, upload_url, upload_headers, content = client.calls[2]
    assert upload_url == "https://r2.example/put"
    assert upload_headers == {"x": "y"}
    assert content == b"<html>hi</html>"


def test_upload_defaults_type_to_dashboard(tmp_path, fake_client):
    html_path = tmp_path / "report.html"
    html_path.write_text("<html></html>")

    upload(html_path, title="No type given")

    _, create_discovery_body = fake_client["client"].calls[0]
    assert create_discovery_body["type"] == "dashboard"


def test_upload_merges_captured_git_info_into_the_version_fields(tmp_path, fake_client, monkeypatch):
    monkeypatch.setattr(
        _upload_module,
        "capture_git_info",
        lambda: {"git_commit": "abc123", "git_branch": "main", "git_repo": None, "git_dirty": False},
    )
    html_path = tmp_path / "report.html"
    html_path.write_text("<html></html>")

    upload(html_path, title="T")

    _, _discovery_id, version_fields = fake_client["client"].calls[1]
    assert version_fields["git_commit"] == "abc123"
    assert version_fields["git_branch"] == "main"


def test_upload_skips_git_capture_when_disabled(tmp_path, fake_client, monkeypatch):
    calls = []
    monkeypatch.setattr(_upload_module, "capture_git_info", lambda: calls.append(1))
    html_path = tmp_path / "report.html"
    html_path.write_text("<html></html>")

    upload(html_path, title="T", capture_git=False)

    assert calls == []


def test_upload_accepts_a_plotly_like_figure_instead_of_a_path(fake_client):
    fig = _FakeFigure()

    result = upload(fig, title="Figure upload")

    assert result.url == "https://discovery.example.com/d/abc"
    _, _discovery_id, _version_id, size_bytes = fake_client["client"].calls[3]
    assert size_bytes == len(b"<html>fig</html>")


def test_upload_sends_a_sha256_content_hash_of_the_actual_bytes(tmp_path, fake_client):
    import hashlib

    html_path = tmp_path / "report.html"
    html_path.write_text("<html>hi</html>")

    upload(html_path, title="T")

    _, _discovery_id, version_fields = fake_client["client"].calls[1]
    assert version_fields["content_hash"] == hashlib.sha256(b"<html>hi</html>").hexdigest()


def test_upload_short_circuits_on_already_exists_without_uploading_or_finalizing(tmp_path, monkeypatch):
    """When the server recognizes the content_hash (see createVersion()'s
    own dedup shortcuts) it returns already_exists=True with the finished
    result inline -- upload() must treat that as done, not proceed to PUT
    bytes or call finalize_version() at all."""

    class _DedupingFakeClient(_FakeClient):
        def create_version(self, discovery_id, **fields):
            self.calls.append(("create_version", discovery_id, fields))
            return {"version_id": "ver-existing", "already_exists": True, "url": "https://discovery.example.com/d/existing", "version": 3, "title": "T"}

        def upload_artifact(self, *args, **kwargs):
            raise AssertionError("upload_artifact() must not be called when already_exists is True")

        def finalize_version(self, *args, **kwargs):
            raise AssertionError("finalize_version() must not be called when already_exists is True")

    holder = {}

    def factory(token, api_url=None, timeout=30.0):
        client = _DedupingFakeClient(token, api_url, timeout)
        holder["client"] = client
        return client

    monkeypatch.setattr(_upload_module, "DiscoveryClient", factory)
    monkeypatch.setattr(_upload_module, "resolve_token", lambda explicit: explicit or "tamdisc_test")
    monkeypatch.setattr(_upload_module, "capture_git_info", lambda: {})

    html_path = tmp_path / "report.html"
    html_path.write_text("<html>hi</html>")

    result = upload(html_path, title="T")

    assert result.url == "https://discovery.example.com/d/existing"
    assert result.id == "ver-existing"
    assert result.version == 3
    assert [call[0] for call in holder["client"].calls] == ["create_discovery", "create_version"]
