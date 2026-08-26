import pytest

from tam.discovery.cli import _build_parser, _parse_metadata, main


def test_publish_subcommand_parses_all_flags():
    parser = _build_parser()

    args = parser.parse_args(
        [
            "publish", "report.html", "--title", "T", "--type", "report", "--name", "n",
            "--description", "d", "--tag", "a", "--tag", "b", "--source", "src.py",
            "--metadata-json", '{"k": 1}', "--no-git", "--token", "tok", "--api-url", "https://x",
        ]
    )

    assert args.command == "publish"
    assert args.path == "report.html"
    assert args.title == "T"
    assert args.type == "report"
    assert args.name == "n"
    assert args.description == "d"
    assert args.tags == ["a", "b"]
    assert args.source_file == "src.py"
    assert args.metadata_json == '{"k": 1}'
    assert args.no_git is True
    assert args.token == "tok"
    assert args.api_url == "https://x"


def test_publish_defaults_type_to_dashboard_and_no_git_to_false():
    parser = _build_parser()

    args = parser.parse_args(["publish", "report.html", "--title", "T"])

    assert args.type == "dashboard"
    assert args.no_git is False
    assert args.tags == []


def test_publish_requires_a_title():
    parser = _build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["publish", "report.html"])


def test_login_subcommand_parses():
    parser = _build_parser()

    args = parser.parse_args(["login", "--token", "tok", "--api-url", "https://x"])

    assert args.command == "login"
    assert args.token == "tok"
    assert args.api_url == "https://x"


def test_list_subcommand_parses_filters():
    parser = _build_parser()

    args = parser.parse_args(["list", "-q", "term", "--tag", "t", "--type", "ty", "--creator", "c", "--sort", "newest"])

    assert args.command == "list"
    assert args.q == "term"
    assert args.tag == "t"
    assert args.type == "ty"
    assert args.creator == "c"
    assert args.sort == "newest"


def test_list_defaults_sort_to_updated():
    parser = _build_parser()

    args = parser.parse_args(["list"])

    assert args.sort == "updated"


def test_info_and_versions_subcommands_take_a_name_positional():
    parser = _build_parser()

    assert parser.parse_args(["info", "my-slug"]).name == "my-slug"
    assert parser.parse_args(["versions", "my-slug"]).name == "my-slug"


def test_parse_metadata_accepts_an_inline_json_object():
    assert _parse_metadata('{"a": 1}') == {"a": 1}


def test_parse_metadata_accepts_an_at_file_reference(tmp_path):
    path = tmp_path / "meta.json"
    path.write_text('{"b": 2}')

    assert _parse_metadata(f"@{path}") == {"b": 2}


def test_parse_metadata_returns_empty_dict_when_omitted():
    assert _parse_metadata(None) == {}


def test_parse_metadata_rejects_invalid_json():
    with pytest.raises(SystemExit):
        _parse_metadata("not json")


def test_parse_metadata_rejects_a_non_object_json_value():
    with pytest.raises(SystemExit):
        _parse_metadata("[1, 2, 3]")


def test_main_expands_the_bare_positional_shorthand_to_the_publish_subcommand(monkeypatch):
    captured = {}

    def fake_cmd_publish(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr("tam.discovery.cli.cmd_publish", fake_cmd_publish)

    exit_code = main(["report.html", "--title", "Hi"])

    assert exit_code == 0
    assert captured["args"].command == "publish"
    assert captured["args"].path == "report.html"
    assert captured["args"].title == "Hi"


def test_main_does_not_expand_a_real_subcommand_name():
    parser = _build_parser()
    # Sanity check for main()'s own "is argv[0] a known subcommand" test --
    # "login" must never get treated as a bare artifact path.
    assert parser.parse_args(["login"]).command == "login"


def test_main_reports_a_clean_error_instead_of_a_traceback_on_failure(monkeypatch, tmp_path):
    monkeypatch.delenv("TAM_PAT", raising=False)
    monkeypatch.setattr("tam.discovery.auth.token_file_path", lambda: tmp_path / "does-not-exist")
    html_path = tmp_path / "r.html"
    html_path.write_text("<html></html>")

    exit_code = main([str(html_path), "--title", "T"])

    assert exit_code == 1


def test_main_prints_help_and_returns_1_with_no_arguments(capsys):
    exit_code = main([])

    assert exit_code == 1
    assert "usage" in capsys.readouterr().out.lower()
