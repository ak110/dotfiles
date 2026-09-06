# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
"""atk (agent-toolkit `atk wi`) のadopt/reject/rm/edit・パストラバーサル検証のテスト。

adopt・reject・rm・editサブコマンドと、ファイル名引数の不正値拒否の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_wi_list_test.py`・
`_atk_wi_show_test.py`・`_atk_wi_process_loop_test.py`に分離する。
位置引数の重複除去（FB7）テストは`too-many-lines`回避のため`_atk_wi_dedup_test.py`へ分離する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import argparse
import contextlib
import datetime
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _managed_temp  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
)  # noqa: E402  # pylint: disable=wrong-import-position

from _atk.wi import (  # pylint: disable=wrong-import-position
    common,  # noqa: E402  # pylint: disable=wrong-import-position
    mutations,  # noqa: E402  # pylint: disable=wrong-import-position
    user_comment,  # noqa: E402  # pylint: disable=wrong-import-position
    uwi,  # noqa: E402  # pylint: disable=wrong-import-position
)
from _atk.wi import frontmatter as frontmatter_parser  # noqa: E402  # pylint: disable=wrong-import-position

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
_USER_COMMENT_ERROR = user_comment.AGENT_USER_COMMENT_EDIT_ERROR + "\n"


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """編集テストをホスト側のエージェント環境と一時rootから隔離する。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setattr(_managed_temp.tempfile, "gettempdir", lambda: str(temp_root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _write_uwi_entry(
    notes: pathlib.Path,
    filename: str,
    *,
    question: str = "変更前の質問",
    answer: str = "既存回答",
    frontmatter: str = "target_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form",
) -> pathlib.Path:
    """非対話edit用のUWIエントリを書き込む。"""
    path = notes / "inbox" / filename
    path.write_text(
        f"---\n{frontmatter}\n---\n\n"
        f"{uwi.QUESTION_HEADING}\n\n{question}\n\n"
        f"{uwi.ANSWER_HEADING}\n\n{uwi.ANSWER_MARKER}\n{answer}\n",
        encoding="utf-8",
    )
    return path


def _disable_transition_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """状態遷移テストからprivate-notesのgit操作を除外する。"""
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)


def _write_convert_plan(directory: pathlib.Path, target_commit: str) -> pathlib.Path:
    """変換テスト用の計画ファイルを作成する。"""
    directory.mkdir(parents=True, exist_ok=True)
    plan = directory / "plan.md"
    plan.write_text(
        f"# 計画\n\n## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{target_commit}`\n",
        encoding="utf-8",
    )
    return plan


def _write_integration_plan(
    directory: pathlib.Path,
    target_commit: str,
    filenames: tuple[str, ...],
) -> pathlib.Path:
    """計画型変換テスト用に関連WIを持つ計画を作成する。"""
    directory.mkdir(parents=True, exist_ok=True)
    plan = directory / "plan.md"
    related_wi = "".join(f"  - {filename}: 変換対象の要求\n" for filename in filenames)
    plan.write_text(
        f"# 計画\n\n## 背景\n\n### 計画メタ情報\n\n- 関連WI:\n{related_wi}- ベースコミット: `{target_commit}`\n",
        encoding="utf-8",
    )
    return plan


def _write_legacy_integration_plan(
    tmp_path: pathlib.Path,
    _target_commit: str,
    filenames: tuple[str, ...],
) -> pathlib.Path:
    """計画型変換テスト用に提示素材を持つ旧書式の計画を作成する。"""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## 提示素材\n\n" + "".join(f"- {name}\n" for name in filenames),
        encoding="utf-8",
    )
    return plan


def _write_convert_awi(
    notes: pathlib.Path,
    filename: str,
    *,
    entry_type: str = "awi",
    state: str = "inbox",
    target_repo: str = "github.com/example/foo",
    target_commit: str = "a" * 40,
    schedule_mapping: str = "",
) -> pathlib.Path:
    """変換テスト用のエントリを書き込む。"""
    directory = notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(
        f"---\ntarget_repo: {target_repo}\ntype: {entry_type}\ntarget_commit: {target_commit}\n{schedule_mapping}---\n\n本文\n",
        encoding="utf-8",
    )
    return path


def _patch_integration_target_resolution(monkeypatch: pytest.MonkeyPatch, target_commit: str = "b" * 40) -> None:
    """hold統合テストの対象worktreeと計画ベースcommit解決を固定する。"""
    monkeypatch.setattr(mutations, "_local_worktree_repo_id", lambda _path: "github.com/example/foo")
    monkeypatch.setattr(mutations, "_resolve_plan_base_commit", lambda _plan, _worktree: target_commit)


def _disable_convert_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """変換テストでprivate-notesへのgit操作を無効化する。"""
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mutations, "_assert_conversion_paths_clean", lambda _path, _paths: None)
    monkeypatch.setattr(mutations, "_assert_conversion_targets_tracked", lambda _path, _paths: None)
    monkeypatch.setattr(mutations, "_git_head", lambda _path: "a" * 40)


def _initialize_private_notes_git(notes: pathlib.Path) -> str:
    """変換失敗時の作業ツリーとindex復元を検証するGit管理repoを作成する。"""
    for state in common.WI_STATES:
        (notes / state).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(notes), "init", "--initial-branch=main"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "-C", str(notes), "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(notes),
            "-c",
            "user.email=agent-toolkit@test.invalid",
            "-c",
            "user.name=agent-toolkit-test",
            "commit",
            "-m",
            "test: initialize private notes",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _disable_real_convert_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """実Git変換テストでremote同期だけを無効化する。"""
    monkeypatch.setattr(mutations, "_push_pending_commits", lambda _path: None)
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)


__all__ = [
    "_AGENT_ENVIRONMENT_VARIABLES",
    "_USER_COMMENT_ERROR",
    "_disable_convert_git",
    "_disable_real_convert_network",
    "_disable_transition_git",
    "_initialize_private_notes_git",
    "_isolate_environment",
    "_patch_integration_target_resolution",
    "_write_convert_awi",
    "_write_convert_plan",
    "_write_integration_plan",
    "_write_legacy_integration_plan",
    "_write_uwi_entry",
]
