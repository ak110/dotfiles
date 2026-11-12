"""plansの分割試験で共有する準備と補助処理。"""

from __future__ import annotations

import os
import pathlib

import pytest

from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._testing import git_repository


@pytest.fixture(autouse=True)
def _isolate_state_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIXとWindowsの状態ディレクトリ解決をテスト固有の場所へ隔離する。"""
    state_root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("LOCALAPPDATA", str(state_root))
    monkeypatch.setenv("APPDATA", str(state_root))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", _OWNER_SESSION)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


_OWNER_SESSION = "plans-test-session"

"""テスト中に計画の所有記録へ書かれるセッション識別子。"""


def _init_local_notes(root: pathlib.Path) -> None:
    """remote不要のlocal-only private-notesを初期化する。"""
    root.mkdir()
    git_repository.init_repository(root, initial_branch="main")
    git_repository.run_git(root, "config", "user.name", "plans-test")
    git_repository.run_git(root, "config", "user.email", "plans-test@example.invalid")
    git_repository.run_git(root, "config", "core.quotePath", "false")
    git_repository.run_git(root, "commit", "--allow-empty", "-m", "base")


def _init_remote_notes(root: pathlib.Path, remote: pathlib.Path) -> None:
    """upstream付きprivate-notesを初期化する。"""
    _init_local_notes(root)
    git_repository.init_bare_repository(remote)
    git_repository.run_git(root, "remote", "add", "origin", str(remote))
    git_repository.run_git(root, "push", "--set-upstream", "origin", "main")


def _clone_notes(remote: pathlib.Path, clone: pathlib.Path) -> None:
    """並行更新用のprivate-notes cloneを作成する。"""
    git_repository.run_git(remote.parent, "clone", str(remote), str(clone))
    git_repository.run_git(clone, "config", "user.name", "plans-test-clone")
    git_repository.run_git(clone, "config", "user.email", "plans-test-clone@example.invalid")
    git_repository.run_git(clone, "config", "core.quotePath", "false")


def _preserved_times(path: pathlib.Path) -> tuple[float | None, int]:
    """取得できる作成日時と更新日時を返す。"""
    birth = _plan_file._creation_epoch(path)  # pylint: disable=protected-access
    return birth, path.stat().st_mtime_ns


def _assert_preserved_times(path: pathlib.Path, expected: tuple[float | None, int]) -> None:
    """作成日時を取得できる環境では作成日時も含めて一致を確認する。"""
    birth, mtime_ns = expected
    assert path.stat().st_mtime_ns == mtime_ns
    if birth is not None:
        assert _plan_file._creation_epoch(path) == birth  # pylint: disable=protected-access


def _set_stable_mtime(path: pathlib.Path) -> tuple[float | None, int]:
    """日時が維持されることを確かめるため更新日時を固定して返す。"""
    timestamp_ns = 1_700_000_000_123_456_789
    os.utime(path, ns=(timestamp_ns, timestamp_ns))
    return _preserved_times(path)


def _assert_preserved_birth(path: pathlib.Path, expected_birth: float | None) -> None:
    """取得できる環境では作成日時だけの維持を確認する。"""
    if expected_birth is not None:
        assert _plan_file._creation_epoch(path) == expected_birth  # pylint: disable=protected-access


def _create_saved_plan(notes: pathlib.Path, relative: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """commit済みのメイン・詳細計画を作成する。"""
    main = notes / "plans" / relative
    detail = main.with_name(main.stem + ".detail.md")
    main.parent.mkdir(parents=True)
    main.write_text("# saved main\n", encoding="utf-8")
    detail.write_text("# saved detail\n", encoding="utf-8")
    git_repository.run_git(notes, "add", "plans")
    git_repository.run_git(notes, "commit", "-m", "add saved plan")
    return main, detail


_SERIES_HEAD = "修正系列の開始時のHEAD"

"""表の名前の識別子の呼称。

`agent-toolkit:process-wi/references/ci-fix-series.md`がこの呼称で表を名付け、
`retired_terms_invariant_test.py`が撤去した旧呼称の置き換え先として記録する。
"""


def _assert_guides_series_head_name(text: str) -> None:
    """案内文が表の名前を修正系列の開始時のHEADで示し、原因commitで示さないことを確かめる。

    原因commitは再帰的なCI失敗のたびに変わるため、その短縮OIDで名付けると同じ修正系列の表を継続できない。
    """
    assert f"ci-<{_SERIES_HEAD}" in text
    assert "原因commit" not in text


_PROGRESS_PLAN_TEMPLATE = """# plan

## 進捗ログ（実行時）

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
{rows}"""


def _write_saved_progress_plan(notes: pathlib.Path, relative: pathlib.Path, rows: str) -> pathlib.Path:
    """進捗ログを持つ保存済みメイン計画を作成する。"""
    main = notes / "plans" / relative
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text(_PROGRESS_PLAN_TEMPLATE.format(rows=rows), encoding="utf-8")
    return main
