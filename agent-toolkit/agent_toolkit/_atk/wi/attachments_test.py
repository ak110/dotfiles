# pylint: disable=protected-access

import pathlib
import time

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config_settings
from agent_toolkit._atk.wi import attachments, bulk, sync
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.wi_entry_files import entry_path

leaf_git_commands = git.leaf_git_commands

_NAME = "20261125-101303-001.md"


def _command(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    with pytest.raises(SystemExit) as exited:
        atk.main(["wi", *args], home=notes.parent)
    assert exited.value.code == 0


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.parametrize("single", [False, True], ids=["bulk", "single"])
def test_attachments_sync_and_remove(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, single: bool
) -> None:
    upstream = git.init_bare_repository(tmp_path / "upstream")
    first = git.init_repository(tmp_path / "first", initial_branch="main", origin=str(upstream))
    wi = entry_path(first, _NAME, state="inbox")
    wi.write_text("---\ntarget_repo: github.com/example/foo\ntype: awi\nstate: inbox\n---\n\n# 本文\n", encoding="utf-8")
    git.commit_all(first, "WI")
    git.run_git(first, "push", "-u", "origin", "main")
    second = tmp_path / "second"
    git.run_git(tmp_path, "clone", str(upstream), str(second))

    log = tmp_path / "log.txt"
    log.write_bytes(b"test log")
    image = tmp_path / "image.png"
    image.write_bytes(b"test bytes")
    _command(first, monkeypatch, "attach", _NAME, str(log), str(image))
    _command(second, monkeypatch, "attachments", _NAME)
    assert "attachments/20261125-101303-001/log.txt" in capsys.readouterr().out
    _command(second, monkeypatch, "show", "--skip-pull", _NAME)
    assert "添付: log.txt (attachments/20261125-101303-001/log.txt)" in capsys.readouterr().out
    assert attachments.read_attachment(second, _NAME, log.name) == (b"test log", None)

    _command(second, monkeypatch, "detach", _NAME, log.name)
    with sync.repo_lock(first):
        sync.pull(first)
        assert [path.name for path in attachments.paths(first, _NAME)] == [image.name]
        if not single:
            bulk._remove_candidates(first, [(wi, "", "", "inbox", "awi")], note=None)  # noqa: SLF001
    if single:
        _command(first, monkeypatch, "rm", _NAME, "--target-repo=github.com/example/foo")
        assert not attachments.paths(first, _NAME)
        expected = git.git_output(first, "rev-parse", "HEAD")
        deadline = time.monotonic() + 10
        while git.git_output(upstream, "rev-parse", "HEAD") != expected and time.monotonic() < deadline:
            time.sleep(0.02)
        assert git.git_output(upstream, "rev-parse", "HEAD") == expected
    with sync.repo_lock(second):
        sync.pull(second)
    assert not attachments.paths(second, _NAME)
    assert (second / "attachments/cumulative-bytes").read_text(encoding="ascii").split()[0] == "18"
    assert not git.git_output(second, "status", "--porcelain")


@pytest.mark.parametrize(
    ("key", "sizes"),
    [
        ("wi_attachment_file_mib", [1048577]),
        ("wi_attachment_item_mib", [600000, 600000]),
        ("wi_attachment_total_mib", [600000, 600000]),
    ],
)
def test_rejects_over_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, key: str, sizes: list[int]) -> None:
    monkeypatch.setenv(config_settings.config_env_name(key), "1")
    sources = []
    for i, size in enumerate(sizes):
        source = tmp_path / f"log{i}.txt"
        source.write_bytes((b"test log\n" * size)[:size])
        sources.append(source)
    with pytest.raises(ValueError, match=key):
        attachments._add(tmp_path, _NAME, sources)  # noqa: SLF001
    assert not (tmp_path / "attachments").exists()


def test_rejects_secret(tmp_path: pathlib.Path) -> None:
    source = tmp_path / "log.txt"
    source.write_text("password=fake-password", encoding="utf-16")
    with pytest.raises(ValueError, match="秘匿値"):
        attachments._add(tmp_path, _NAME, [source])  # noqa: SLF001


def test_rejects_symlink_attachment_parent(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "log.txt").write_text("本文", encoding="utf-8")
    try:
        (tmp_path / "attachments").symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"この環境ではシンボリックリンクを作成できない: {error}")
    with pytest.raises(ValueError, match="通常のファイルまたはディレクトリ以外"):
        attachments.paths(tmp_path, _NAME)
