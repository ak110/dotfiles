"""WIのmutations transitions試験で共有する準備処理。"""

import contextlib
import io
import pathlib

import pytest

from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import user_comment
from agent_toolkit._testing.wi_entry_files import entry_path as _test_entry_path
from agent_toolkit._testing.wi_mutations_support import _disable_transition_git
from agent_toolkit.atk_test import _write_awi_file

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")


_USER_COMMENT_ERROR = "失敗: " + user_comment.AGENT_USER_COMMENT_EDIT_ERROR


def _set_state(path: pathlib.Path, state: str) -> pathlib.Path:
    """固定パスを保ってテスト項目の状態を更新する。"""
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    metadata, body = parsed
    metadata["state"] = state
    path.write_text(frontmatter_parser.serialize_frontmatter(metadata, body), encoding="utf-8")
    return path


def _write_body_file(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """CLIへ渡す本文ファイルを作成する。"""
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    return body_file


def _place_awi(
    notes: pathlib.Path, filename: str, state: str, body: str = "テスト本文", *, legacy_layout: bool = False
) -> pathlib.Path:
    """AWIを指定状態へ設定し、旧配置の読取検証では状態ディレクトリへ置く。"""
    source = _write_awi_file(notes, filename, body=body)
    if legacy_layout:
        target = _test_entry_path(notes, filename, state=state, legacy_layout=True)
        source.replace(target)
        return _set_state(target, state)
    return _set_state(source, state)


_TERMINAL_DIRECTORIES = {"adopt": "adopted", "reject": "rejected"}


class _BulkTtyInput(io.StringIO):
    """対話端末として応答するテスト用標準入力。"""

    def isatty(self) -> bool:
        """対話端末であることを返す。"""
        return True


def _write_bulk_entry(
    notes: pathlib.Path,
    state: str,
    filename: str,
    *,
    target_repo: str = "github.com/example/foo",
) -> pathlib.Path:
    """一括操作の検証に使うエントリを指定状態へ書き込む。"""
    path = _test_entry_path(notes, filename)
    path.write_text(f"---\nstate: {state}\ntarget_repo: {target_repo}\ntype: awi\n---\n\nテスト本文\n", encoding="utf-8")
    return path


def _patch_bulk_git(monkeypatch: pytest.MonkeyPatch, commit_calls: list[str]) -> None:
    """一括操作が呼び出す2モジュールのgit操作を抑止し、commitメッセージを記録する。"""
    _disable_transition_git(monkeypatch)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(
        _wi_sync,
        "commit_and_push",
        lambda _private_notes, message, _paths, **_kwargs: commit_calls.append(message),
    )


def _write_bulk_uwi(notes: pathlib.Path, state: str, filename: str) -> pathlib.Path:
    """一括操作の検証に使うUWIを指定状態へ作成する。"""
    path = _test_entry_path(notes, filename)
    path.write_text(
        f"---\nstate: {state}\ntarget_repo: github.com/example/foo\ntype: uwi\n---\n\n## 質問\n\n確認事項\n\n## 回答\n",
        encoding="utf-8",
    )
    return path


def _set_agent_environment(monkeypatch: pytest.MonkeyPatch, *, agent: bool) -> None:
    """エージェント環境の判定に使う環境変数をそろえる。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")
