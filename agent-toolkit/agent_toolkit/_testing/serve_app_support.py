"""serveのapp試験が共有する入力と補助処理。"""

# pylint: disable=protected-access

import contextlib
import datetime
import pathlib
import subprocess
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.serve_support import _patch_batch_repo_operations


def _fixed_path(root: pathlib.Path, filename: str) -> pathlib.Path:
    """任意名の試験入力を公開APIで年月へ解決し、親ディレクトリを用意する。"""
    path = entry_path(root, filename, fallback_date=datetime.date(2026, 10, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_REBASE_UWI = (
    "---\nstate: inbox\ntype: uwi\ntarget_repo: github.com/example/repo\nquestion_type: yes-no\n---\n\n"
    "## 質問\n\n進めてよいか？\n\n"
    "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n"
)


_REBASE_AWI = "---\nstate: inbox\ntype: awi\ntarget_repo: github.com/example/repo\n---\n\n本文\n"


_REBASE_AGENT_AWI = "---\nstate: inbox\ntype: awi\ntarget_repo: github.com/example/repo\nsource: session-review\n---\n\n本文\n"


def _rebasing_notes(tmp_path: pathlib.Path, make_clean_repo: typing.Callable[..., pathlib.Path]) -> pathlib.Path:
    """各状態の項目をcommitし、rebaseの中間状態が残るWI保存リポジトリを作成する。"""
    notes = make_clean_repo(tmp_path, "notes")
    for state_name, filename, text in (
        ("inbox", "entry.md", _REBASE_AWI),
        ("inbox", "question.md", _REBASE_UWI),
        ("inbox", "agent.md", _REBASE_AGENT_AWI),
        ("processing", "working.md", _REBASE_AWI),
        ("hold", "held.md", _REBASE_AWI),
    ):
        _fixed_path(notes, filename).write_text(text.replace("state: inbox", f"state: {state_name}"), encoding="utf-8")
    subprocess.run(["git", "-C", str(notes), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(notes), "commit", "-m", "setup"], check=True, capture_output=True)
    rebase_dir = subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "--git-path", "rebase-merge"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    (notes / rebase_dir).mkdir(parents=True)
    return notes


def _notes_snapshot(notes: pathlib.Path) -> tuple[dict[str, bytes], str, str]:
    """作業ツリーのファイル、HEADおよび作業ツリーの状態を返す。"""
    files = {
        str(path.relative_to(notes)): path.read_bytes()
        for path in notes.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
    head = subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    status = subprocess.run(
        ["git", "-C", str(notes), "status", "--porcelain=v1", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    return files, head, status


_OBSERVATION_RECORD = "## 反映後の観測の再開記録\n\n- 再開区分: 反映後の観測だけが残る\n- 計画: 計画なし\n"


_SINGLE_SHOW_TEXT = (
    "## target_repo: github.com/example/foo\n"
    "### keep.md [inbox]\n---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文  \n"
)


def _patch_single_add_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    """単件登録と一括取り込みのどちらの処理でもロック・remote同期・commitを無効化する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    _patch_batch_repo_operations(monkeypatch)


def _serve_app(root: pathlib.Path) -> typing.Any:
    """指定したキュー管理ディレクトリを使う`atk serve`のアプリを返す。"""
    root.mkdir(parents=True, exist_ok=True)
    return serve_app.create_app(root, config.ServeConfig("127.0.0.1", 28766), state.ServeState(root))


def _saved_files(root: pathlib.Path) -> dict[str, str]:
    """年月配置へ保存されたファイル名と内容を返す。"""
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))}
