"""WIのuwi試験で共有する準備処理。"""

import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.git_fakes import _FIXED_HEAD_COMMIT
from agent_toolkit.atk_test import _FIXED_DT

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")


@pytest.fixture(autouse=True)
def _clear_agent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """回答テストをホスト側のエージェント環境変数から隔離する。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _body_file_args(tmp_path: pathlib.Path, body: str) -> list[str]:
    """本文ファイルを作成してCLI引数を返す。"""
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    return ["--body-file", str(body_file)]


def _invoke_uwi_add(tmp_path: pathlib.Path, *args: str, body: str) -> SystemExit:
    """本文ファイル経由でUWI投入を呼び出し、終了結果を返す。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", "add", "--type=uwi", "--question-type=yes-no", *args, *_body_file_args(tmp_path, body)],
            home=tmp_path,
            now=_FIXED_DT,
        )
    return exc_info.value


def _make_uwi_add_fake(myrepo: pathlib.Path) -> Callable[..., subprocess.CompletedProcess[Any]]:
    """UWI投入検証用fake_runを生成する。`myrepo`のorigin URLのみ実URLを返し、それ以外は空応答を返す。"""

    def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            stdout: Any = (
                "https://github.com/example/myrepo.git\n" if kw.get("text") else b"https://github.com/example/myrepo.git\n"
            )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
            stdout = "true\n" if kw.get("text") else b"true\n"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            stdout = f"{_FIXED_HEAD_COMMIT}\n" if kw.get("text") else f"{_FIXED_HEAD_COMMIT}\n".encode()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
        empty: Any = "" if kw.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run
