import pathlib
import subprocess
import sys
from typing import Any

import check_file_lines
import pytest
from agent_toolkit._testing.git_repository import init_repository, run_git


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("file.md", b"x\n" * 250, 0),
        ("file.md", b"x\n" * 251, 1),
        ("file.py", b"x\n" * 1000, 0),
        ("file.py", b"x\n" * 1001, 1),
        ("file.md", b"x\n" * 250 + b"last", 0),
        ("file.md", b"x\n" * 251 + b"last", 1),
        ("file.md", b"x\r\n" * 250, 0),
        ("file.md", b"x\r\n" * 251, 1),
        ("file.md", b"", 0),
        ("file.py", b"\0" + b"x\n" * 1001, 0),
        ("日本語 空白.md", b"x\n" * 251, 1),
        ("rules.md.tmpl", b"x\n" * 250, 0),
        ("rules.md.tmpl", b"x\n" * 251, 1),
    ],
)
def test_line_boundaries(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], name: str, content: bytes, expected: int
) -> None:
    repository = init_repository(tmp_path)
    (repository / name).write_bytes(content)
    run_git(repository, "add", "--", name)

    assert check_file_lines.check_repository(repository) == expected
    output = capsys.readouterr()
    assert not output.err
    if expected == 0:
        assert not output.out
        return

    limit, after = (250, 150) if name.endswith((".md", ".md.tmpl")) else (1000, 600)
    assert output.out.count("\n") == 1
    assert output.out.startswith(f"{name}:1: ")
    for element in (f"{content.count(b'\n')}行", f"{limit}行", f"{after}行", "次の操作"):
        assert element in output.out


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("linguist-generated", 0),
        ("linguist-generated=true", 0),
        ("linguist-vendored", 0),
        ("-linguist-generated", 1),
        ("linguist-generated=false", 1),
    ],
)
def test_attributes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], attribute: str, expected: int) -> None:
    repository = init_repository(
        tmp_path,
        files={
            ".gitattributes": f"generated.md {attribute}\nrecord.json linguist-generated\nnotice.md linguist-vendored\n",
            "generated.md": "x\n" * 251,
            "record.json": "x\n" * 1001,
            "notice.md": "x\n" * 251,
        },
    )
    run_git(repository, "add", ".")
    assert check_file_lines.check_repository(repository) == expected
    output = capsys.readouterr()
    assert not output.err
    assert bool(output.out) is bool(expected)
    if expected:
        assert output.out.startswith("generated.md:1:")
        assert output.out.count("\n") == 1


def test_symlink_and_untracked_files(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = init_repository(tmp_path)
    (repository / "untracked.md").write_bytes(b"x\n" * 251)
    (repository / "link.md").symlink_to("untracked.md")
    run_git(repository, "add", "link.md")
    assert check_file_lines.check_repository(repository) == 0
    output = capsys.readouterr()
    assert not output.out and not output.err


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ("[short].md", 0),
        ("s.md", 1),
        (".gitattributes", 1),
        ("nested/.gitattributes", 1),
        ("pyproject.toml", 1),
        ("scripts/check_file_lines.py", 1),
    ],
)
def test_selected_paths_keep_repository_wide_checks_for_policy_changes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], selected: str, expected: int
) -> None:
    repository = init_repository(tmp_path, files={"[short].md": "ok\n", "s.md": "x\n" * 251})
    run_git(repository, "add", ".")
    assert check_file_lines.check_repository(repository, [repository / selected]) == expected
    output = capsys.readouterr()
    assert not output.err
    assert output.out.startswith("s.md:1:") == bool(expected)


@pytest.mark.parametrize("query", ["rev-parse", "ls-files", "check-attr"])
def test_git_query_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], query: str
) -> None:
    """Gitの各照会失敗は理由・復旧操作を示して終了2になる。"""
    repository = init_repository(tmp_path)
    original = subprocess.run

    def fail_query(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if query in command:
            return subprocess.CompletedProcess(command, 128, b"", b"query failed")
        return original(command, check=kwargs.pop("check", False), **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_query)
    assert check_file_lines.check_repository(repository) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "Gitの照会に失敗" in output.err
    assert "query failed" in output.err
    assert "次の操作:" in output.err


def test_cli_from_subdirectory(tmp_path: pathlib.Path, host_environ) -> None:
    repository = init_repository(tmp_path, files={"root.md": "x\n" * 251})
    run_git(repository, "add", ".")
    directory = repository / "nested"
    directory.mkdir()
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(check_file_lines.__file__).resolve())],
        cwd=directory,
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert result.returncode == 1
    assert result.stdout.startswith("root.md:1: 251行が上限250行")
    assert not result.stderr


def test_main_outside_git(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Git作業ツリー外の公開入口は検査失敗として終了2を返す。"""
    monkeypatch.setattr(pathlib.Path, "cwd", lambda: tmp_path)
    assert check_file_lines.main([]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "Gitの照会に失敗" in output.err
    assert "次の操作:" in output.err
