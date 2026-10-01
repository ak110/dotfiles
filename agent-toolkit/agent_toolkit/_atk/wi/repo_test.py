"""`_atk/wi/repo.py`の対象リポジトリの解決とtarget_repoの一致判定が失敗した際の出力を検証する。"""

import pathlib
import subprocess

import pytest

from agent_toolkit._atk.wi import repo as repo_module
from agent_toolkit._atk.wi.common import WebInputError


def _failure_and_next_action(stderr: str) -> tuple[str, str]:
    """標準エラーの失敗行と次の操作の行を返す。失敗行が1本だけであることも確かめる。"""
    lines = stderr.splitlines()
    assert [line for line in lines if line.startswith("失敗: ")] == [lines[0]]
    assert lines[1].startswith("次の操作: ")
    return lines[0], lines[1]


def test_resolve_repo_id_or_raise_guides_target_repo_option() -> None:
    """パスでもURLでもない値は、出力せずに`--target-repo`の指定方法を次の操作とする例外を送出する。"""
    with pytest.raises(WebInputError, match="パスが存在せずリモートURLとしても解析できない") as error_info:
        repo_module.resolve_repo_id_or_raise("not-a-repository")

    assert "`--target-repo`へローカルworktreeのパスかremote URLを指定" in error_info.value.next_action


def test_resolve_repo_id_reports_one_failure_line(capsys: pytest.CaptureFixture[str]) -> None:
    """CLIで対象の解決に失敗した場合は失敗行1本と次の操作の行で終了コード2になる。"""
    with pytest.raises(SystemExit) as exc_info:
        repo_module._resolve_repo_id("not-a-repository")  # pylint: disable=protected-access

    assert exc_info.value.code == 2
    failure, next_action = _failure_and_next_action(capsys.readouterr().err)
    assert "not-a-repository" in failure
    assert "--target-repo" in next_action


def test_resolve_head_commit_without_commit_asks_to_check_history(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """コミットが無い作業ツリーでは、コミットの有無を確かめる操作を次の操作として返す。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    with pytest.raises(SystemExit) as exc_info:
        repo_module.resolve_head_commit(tmp_path)

    assert exc_info.value.code == 2
    _failure, next_action = _failure_and_next_action(capsys.readouterr().err)
    assert f"`git -C {tmp_path} log -1`" in next_action


def test_verify_target_repo_content_without_target_repo_guides_edit(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """target_repoを欠いた項目は、`atk wi edit`での追記を次の操作として返す。"""
    path = tmp_path / "20260101-000000-001.md"

    with pytest.raises(SystemExit) as exc_info:
        repo_module._verify_target_repo_content(  # pylint: disable=protected-access
            path, "---\ntype: awi\n---\n\n本文\n", "github.com/example/foo"
        )

    assert exc_info.value.code == 2
    _failure, next_action = _failure_and_next_action(capsys.readouterr().err)
    assert f"atk wi edit {path.name}" in next_action


def test_verify_target_repo_content_mismatch_guides_actual_value(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """target_repoの不一致は、実際の値での`--target-repo`の指定し直しを次の操作として返す。"""
    path = tmp_path / "20260101-000000-001.md"

    with pytest.raises(SystemExit) as exc_info:
        repo_module._verify_target_repo_content(  # pylint: disable=protected-access
            path, "---\ntarget_repo: github.com/example/other\ntype: awi\n---\n\n本文\n", "github.com/example/foo"
        )

    assert exc_info.value.code == 2
    _failure, next_action = _failure_and_next_action(capsys.readouterr().err)
    assert "`--target-repo=github.com/example/other`" in next_action
