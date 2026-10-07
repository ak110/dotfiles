"""`_atk/wi/mutations/`のテストが共有する、WIエントリと計画の作成およびGit操作の差し替え。"""

import contextlib
import pathlib

import pytest

from agent_toolkit._atk.wi import repo, user_comment, uwi
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.mutations import targets as mutation_targets
from agent_toolkit._testing import git_repository

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
_USER_COMMENT_ERROR = "失敗: " + user_comment.AGENT_USER_COMMENT_EDIT_ERROR


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
    """状態遷移テストからprivate-notesのgit操作とカレントディレクトリのリポジトリ解決を除外する。

    `--target-repo`を省略した場合の解決はテスト実行時のカレントディレクトリに依存するため、
    対象リポジトリの一致を明示指定だけで判定する状態へそろえる。
    """
    monkeypatch.setattr(repo, "detect_current_repo_id", lambda: None)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)


def _write_convert_plan(directory: pathlib.Path, target_commit: str) -> pathlib.Path:
    """変換テスト用の計画ファイルを作成する。"""
    directory.mkdir(parents=True, exist_ok=True)
    plan = directory / "plan.md"
    plan.write_text(
        f"# 計画\n\n## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{target_commit}`\n",
        encoding="utf-8",
    )
    return plan


def _edit_plan_args(tmp_path: pathlib.Path, filename: str, body: str, plan: pathlib.Path) -> list[str]:
    """計画型編集を本文ファイル経由で呼ぶCLI引数を返す。"""
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    return [
        "wi",
        "edit",
        filename,
        "--body-file",
        str(body_file),
        "--plan-file",
        str(plan),
        "--target-repo",
        "github.com/example/foo",
    ]


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


def _disable_convert_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """変換テストでprivate-notesへのgit操作を無効化する。"""
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mutation_targets, "_git_head", lambda _path: "a" * 40)


def _init_notes_with_origin(notes: pathlib.Path, remote: pathlib.Path) -> None:
    """private-notesをGitリポジトリにして全ファイルを基準commitへ記録し、bareの`origin`へpushする。"""
    git_repository.init_repository(notes, initial_branch="main", origin=str(remote), commit_message="base")
    git_repository.init_bare_repository(remote)
    git_repository.run_git(notes, "push", "--set-upstream", "origin", "main")
