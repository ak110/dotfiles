"""dotfilesの公開入口がcwdの実装を選び、子の結果を透過させる契約。"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Iterator

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _git(cwd: pathlib.Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-c", "core.hooksPath=", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        timeout=120,
    )


def _write_child(root: pathlib.Path, name: str, status: int) -> None:
    directory = root / "agent-toolkit" / "bin"
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "child.py"
    script.write_text(
        "import json, os, sys\n"
        f"print(json.dumps({{'entry': {name!r}, 'cwd': os.getcwd(), 'args': sys.argv[1:]}}))\n"
        f"print('stderr:{name}', file=sys.stderr)\nraise SystemExit({status})\n",
        encoding="utf-8",
    )
    linux = directory / "atk"
    linux.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    linux.chmod(0o755)
    windows = f'@echo off\r\n"{sys.executable}" "{script}" %*\r\nexit /b %ERRORLEVEL%\r\n'
    (directory / "atk.cmd").write_bytes(windows.encode("cp932"))


@pytest.fixture(name="repositories")
def _repositories(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, pathlib.Path]]:
    """同一リポジトリと別リポジトリを実際のGitで構成し、テストの資源を回収する。"""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    main = tmp_path / "main with spaces"
    main.mkdir()
    _git(main, "init", "--quiet")
    (main / "bin").mkdir()
    for name in ("atk", "atk.cmd"):
        shutil.copy2(_ROOT / "bin" / name, main / "bin" / name)
    _write_child(main, "installed", 0)
    _git(main, "add", ".")
    _git(main, "commit", "--quiet", "-m", "入口のテスト配置")
    worktree = tmp_path / "worktree with spaces"
    _git(main, "worktree", "add", "--quiet", "--detach", str(worktree))
    _write_child(worktree, "changed", 23)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "--quiet")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        yield {"main": main, "worktree": worktree, "other": other, "outside": outside}
    finally:
        _git(main, "worktree", "remove", "--force", str(worktree))
        shutil.rmtree(main)
        shutil.rmtree(other)


def _run(main: pathlib.Path, cwd: pathlib.Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    entry = main / "bin" / ("atk.cmd" if os.name == "nt" else "atk")
    command = ["cmd.exe", "/d", "/c", str(entry), *arguments] if os.name == "nt" else [str(entry), *arguments]
    return subprocess.run(
        command, cwd=cwd, check=False, capture_output=True, encoding="cp932" if os.name == "nt" else "utf-8", timeout=120
    )


@pytest.mark.parametrize(
    "arguments",
    [["review-table", "show"], ["run-script", "plan-check", "--", "value with spaces", "日本語", "-value", "a!b", "100%"]],
)
def test_worktree_and_subdirectory_use_changed_version_and_preserve_results(
    repositories: dict[str, pathlib.Path], arguments: list[str]
) -> None:
    """旧版の成功へ戻らず、同一リポジトリの改修版の拒否と引数を返す。"""
    worktree = repositories["worktree"]
    subdirectory = worktree / "nested"
    subdirectory.mkdir()
    for cwd in (worktree, subdirectory):
        result = _run(repositories["main"], cwd, arguments)
        assert result.returncode == 23
        assert json.loads(result.stdout) == {"entry": "changed", "cwd": str(cwd), "args": arguments}
        assert result.stderr.strip() == "stderr:changed"


@pytest.mark.parametrize("location", ["main", "other", "outside"])
def test_main_and_unrelated_cwd_use_installed_entry(repositories: dict[str, pathlib.Path], location: str) -> None:
    """主作業ツリーとGit外・別Gitの呼び出しは、入口が属する従来版へ到達する。"""
    cwd = repositories[location]
    result = _run(repositories["main"], cwd, ["agents", "list"])
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"entry": "installed", "cwd": str(cwd), "args": ["agents", "list"]}
    assert result.stderr.strip() == "stderr:installed"


def test_missing_worktree_entry_fails_without_using_installed_success(repositories: dict[str, pathlib.Path]) -> None:
    """同一リポジトリの入口不足を、別版の成功で隠さず対処とともに返す。"""
    worktree = repositories["worktree"]
    (worktree / "agent-toolkit" / "bin" / ("atk.cmd" if os.name == "nt" else "atk")).unlink()
    result = _run(repositories["main"], worktree, ["agents", "list"])
    assert result.returncode == 127
    assert not result.stdout
    assert "atkの起動先がありません" in result.stderr
    assert "次の操作:" in result.stderr


def test_windows_entry_preserves_batch_encoding() -> None:
    """実行可能なWindowsバッチの文字コードと行末を保つ。"""
    content = (_ROOT / "bin" / "atk.cmd").read_bytes()
    assert content.decode("cp932").startswith("@echo off\r\n")
    assert b"\n" not in content.replace(b"\r\n", b"")


def test_path_lookup_reaches_worktree_version_before_plugin_entry(repositories: dict[str, pathlib.Path]) -> None:
    """dotfilesの登録順（`bin`の後に`agent-toolkit/bin`）のPATHで、名前だけで起動した`atk`も改修版へ到達する。"""
    main = repositories["main"]
    worktree = repositories["worktree"]
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join([str(main / "bin"), str(main / "agent-toolkit" / "bin"), environment["PATH"]])
    command = ["cmd.exe", "/d", "/c", "atk", "review-table", "show"] if os.name == "nt" else ["atk", "review-table", "show"]
    result = subprocess.run(
        command,
        cwd=worktree,
        env=environment,
        check=False,
        capture_output=True,
        encoding="cp932" if os.name == "nt" else "utf-8",
        timeout=120,
    )
    assert result.returncode == 23
    assert json.loads(result.stdout) == {"entry": "changed", "cwd": str(worktree), "args": ["review-table", "show"]}
