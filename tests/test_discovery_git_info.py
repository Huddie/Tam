import subprocess

from tam.discovery.git_info import capture_git_info


def _init_repo(path):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "a.txt").write_text("hello")
    subprocess.run(["git", "add", "a.txt"], cwd=path, check=True, capture_output=True)
    # --no-gpg-sign: this is a disposable temp repo created purely to exercise
    # capture_git_info()'s subprocess calls -- it has nothing to do with, and
    # shouldn't depend on, the machine's real commit-signing configuration.
    subprocess.run(["git", "commit", "--no-gpg-sign", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_capture_git_info_in_a_real_repo_with_no_remote_and_a_dirty_tree(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("changed after commit")

    info = capture_git_info(tmp_path)

    assert len(info["git_commit"]) == 40
    assert info["git_dirty"] is True
    assert info["git_repo"] is None  # no remote configured -- shouldn't discard the rest


def test_capture_git_info_picks_up_the_configured_remote_and_a_clean_tree(tmp_path):
    _init_repo(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/repo.git"], cwd=tmp_path, check=True, capture_output=True
    )

    info = capture_git_info(tmp_path)

    assert info["git_repo"] == "https://example.com/repo.git"
    assert info["git_dirty"] is False


def test_capture_git_info_returns_an_empty_dict_outside_a_git_repo(tmp_path):
    assert capture_git_info(tmp_path) == {}
