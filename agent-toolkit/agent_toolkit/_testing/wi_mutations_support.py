"""`_atk/wi/mutations/`のテストが共有する、WIエントリと計画の作成およびGit操作の差し替え。"""

import contextlib
import pathlib
import subprocess

import pytest

from agent_toolkit._atk.wi import repo, user_comment, uwi
from agent_toolkit._atk.wi.mutations import content as mutation_content
from agent_toolkit._atk.wi.mutations import dependencies as mutation_dependencies
from agent_toolkit._atk.wi.mutations import targets as mutation_targets
from agent_toolkit._atk.wi.mutations import transitions as mutation_transitions

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")
_USER_COMMENT_ERROR = "失敗: " + user_comment.AGENT_USER_COMMENT_EDIT_ERROR


MUTATION_MODULES = (mutation_content, mutation_dependencies, mutation_targets, mutation_transitions)
"""WI変更処理のサブモジュール。テストが差し替える共通の名前を束縛しうる全モジュール。"""


def setattr_in_mutation_modules(monkeypatch: pytest.MonkeyPatch, name: str, value: object) -> None:
    """`agent_toolkit._atk.wi.common`から複数のサブモジュールへimportした名前を、束縛する全サブモジュールで差し替える。

    各サブモジュールは定義元から名前を自らimportして束縛するため、1つのサブモジュールの差し替えは他へ届かない。
    """
    for module in MUTATION_MODULES:
        if name in vars(module):
            monkeypatch.setattr(module, name, value)


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
    setattr_in_mutation_modules(monkeypatch, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    setattr_in_mutation_modules(monkeypatch, "_push_pending_commits", lambda _path: None)
    setattr_in_mutation_modules(monkeypatch, "_pull", lambda _path: None)
    setattr_in_mutation_modules(monkeypatch, "_commit_and_push", lambda *_args, **_kwargs: None)


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
    setattr_in_mutation_modules(monkeypatch, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    setattr_in_mutation_modules(monkeypatch, "_push_pending_commits", lambda _path: None)
    setattr_in_mutation_modules(monkeypatch, "_pull", lambda _path: None)
    setattr_in_mutation_modules(monkeypatch, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mutation_targets, "_git_head", lambda _path: "a" * 40)


def _init_notes_with_origin(notes: pathlib.Path, remote: pathlib.Path) -> None:
    """private-notesをGitリポジトリにして全ファイルを基準commitへ記録し、bareの`origin`へpushする。"""
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=notes,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    for key, value in (("user.name", "queue-test"), ("user.email", "queue-test@example.invalid")):
        subprocess.run(
            ["git", "config", key, value],
            cwd=notes,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    subprocess.run(
        ["git", "add", "."], cwd=notes, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=notes,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        cwd=remote.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=notes,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    subprocess.run(
        ["git", "push", "--set-upstream", "origin", "main"],
        cwd=notes,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
