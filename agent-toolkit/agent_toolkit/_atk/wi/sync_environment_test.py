# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのsync: environmentのテスト。"""

import os
import pathlib

import filelock
import platformdirs
import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._common import file_lock as _file_lock
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_sync_support import _LEGACY_AWI, _LEGACY_UWI, _init_legacy_repo
from agent_toolkit._testing.wi_sync_support import _remote_sync as _remote_sync


class TestPrivateNotesAutoCreate:
    """`AGENT_TOOLKIT_PRIVATE_NOTES`未設定かつ省略時に使うパスが不在の場合のローカルリポジトリ自動生成を検証する。

    conftestが適用する隔離（`agent_toolkit._testing.isolation`）が全テストへ環境変数を設定するため、
    本クラスの各テストは`monkeypatch.delenv`で明示的に解除してから検証する。
    """

    @pytest.fixture(autouse=True)
    def _isolate_data_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """自動生成先を実環境の`user_data_dir`から隔離し、テストの準備で設定した環境変数による上書きを解除する。"""
        monkeypatch.delenv("AGENT_TOOLKIT_PRIVATE_NOTES", raising=False)
        monkeypatch.setattr(platformdirs, "user_data_dir", lambda _name, **_kwargs: str(tmp_path / "data"))

    def test_private_notes_path_falls_back_to_platformdirs_when_default_missing(self, tmp_path: pathlib.Path) -> None:
        """未指定の場合に使う`home/private-notes`が不在の場合、platformdirs配下へフォールバックする。"""
        home = tmp_path / "home"
        home.mkdir()
        resolved = _private_notes.default_private_notes(home)
        assert resolved == tmp_path / "data" / "private-notes"

    def test_private_notes_path_prefers_existing_default(self, tmp_path: pathlib.Path) -> None:
        """省略時に使うパスが実在する場合はplatformdirsへフォールバックせずそちらを返す。"""
        home = tmp_path / "home"
        (home / "private-notes").mkdir(parents=True)
        resolved = _private_notes.default_private_notes(home)
        assert resolved == home / "private-notes"

    def test_ensure_environment_initializes_local_repo(self, tmp_path: pathlib.Path) -> None:
        """省略時に使うパスが不在の場合、`ensure_environment`はローカルgitリポジトリを自動生成して返す。"""
        home = tmp_path / "home"
        home.mkdir()
        root = _wi_sync.ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        assert root == tmp_path / "data" / "private-notes"
        assert (root / ".git").is_dir()
        assert not (root / ".agent-toolkit-local-only").exists()
        expected_state_dirs = (
            "inbox",
            "processing",
            "adopted",
            "rejected",
            "inbox",
            "adopted",
        )
        for name in expected_state_dirs:
            assert not (root / name).exists()
        exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert exclude.splitlines().count(_file_lock.PLAN_LOCK_IGNORE_PATTERN) == 1
        assert not (root / ".gitignore").exists()
        assert not git_repository.git_output(root, "status", "--porcelain")

    def test_wi_list_initializes_local_repo_without_git_output(
        self,
        tmp_path: pathlib.Path,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """初回の`wi list`はローカルリポジトリを生成してもGitの成功出力を残さず、保存先の警告だけを書く。"""
        home = tmp_path / "home"
        home.mkdir()

        with pytest.raises(SystemExit) as excinfo:
            atk.main(["wi", "list"], home=home)

        captured = capfd.readouterr()
        assert excinfo.value.code == 0
        assert captured.out == ""
        lines = captured.err.splitlines()
        assert lines[0].startswith("警告: ") and lines[1].startswith("次の操作: ")
        assert len(lines) == 2

    @pytest.mark.parametrize(
        ("argv", "stdout"),
        [
            (["wi", "list", "--target-repo=all", "--skip-pull"], ""),
            (["wi", "list", "--target-repo=all", "--skip-pull", "--summary-only"], ""),
            (["wi", "list", "--target-repo=all", "--skip-pull", "--count"], "0\n"),
            (["plans", "list"], ""),
        ],
    )
    def test_wi_list_warns_when_using_empty_default_private_notes(
        self,
        tmp_path: pathlib.Path,
        capfd: pytest.CaptureFixture[str],
        argv: list[str],
        stdout: str,
    ) -> None:
        """省略時に解決した空の保存先を使う場合、標準出力を変えずに保存先と設定方法を標準エラーへ警告する。

        既存のキューが別の場所にあるエンドユーザーは、無出力の結果をキューが空であることと区別できない。
        """
        home = tmp_path / "home"
        home.mkdir()

        with pytest.raises(SystemExit) as excinfo:
            atk.main(argv, home=home)

        captured = capfd.readouterr()
        assert excinfo.value.code == 0
        assert captured.out == stdout
        warning = captured.err
        assert warning.startswith("警告: ")
        assert str(tmp_path / "data" / "private-notes") in warning
        assert "AGENT_TOOLKIT_PRIVATE_NOTES" in warning and "~/private-notes" in warning
        assert "対応不要" in warning

    @pytest.mark.parametrize("condition", ["override", "home-default", "existing-wi", "existing-plan"])
    def test_default_private_notes_warning_conditions(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
        condition: str,
    ) -> None:
        """環境変数の指定、`~/private-notes`の実在、WIか計画を持つ保存先では警告しない。"""
        home = tmp_path / "home"
        home.mkdir()
        if condition == "override":
            notes = tmp_path / "explicit-notes"
            _wi_sync.init_local_private_notes_repo(notes)
            monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
        elif condition == "home-default":
            _wi_sync.init_local_private_notes_repo(home / "private-notes")
        else:
            root = _wi_sync.ensure_environment(home)
            target = root / "2026" / "10" / "item.md" if condition == "existing-wi" else root / "plans" / "2026" / "10" / "p.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# 項目\n", encoding="utf-8")
        capfd.readouterr()

        _wi_sync.ensure_environment(home)

        assert capfd.readouterr().err == ""

    def test_ensure_environment_is_idempotent(self, tmp_path: pathlib.Path) -> None:
        """2回連続で呼んでも2回目は既存のローカルリポジトリをそのまま返す（再初期化しない）。"""
        home = tmp_path / "home"
        home.mkdir()
        first = _wi_sync.ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        marker = first / "sentinel.txt"
        marker.write_text("kept", encoding="utf-8")
        second = _wi_sync.ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        assert second == first
        assert marker.read_text(encoding="utf-8") == "kept"

    def test_ensure_environment_excludes_plan_lock_without_commit(self, tmp_path: pathlib.Path) -> None:
        """計画ロックの除外を版管理の対象外へ記録し、commitも作業ツリーの差分も生じない。"""
        home = tmp_path / "home"
        root = home / "private-notes"
        git_repository.init_repository(root)
        (root / "README.md").write_text("base\n", encoding="utf-8")
        git_repository.commit_all(root, "base")
        original_head = git_repository.git_output(root, "rev-parse", "HEAD")
        lock = root / "plans" / ".agent-toolkit-plan-create.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()

        assert _wi_sync.ensure_environment(home) == root  # pylint: disable=protected-access  # noqa: SLF001
        exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert exclude.splitlines().count(_file_lock.PLAN_LOCK_IGNORE_PATTERN) == 1
        assert not (root / ".gitignore").exists()
        assert git_repository.git_output(root, "rev-parse", "HEAD") == original_head
        assert not git_repository.git_output(root, "status", "--porcelain")

    def test_ensure_environment_keeps_recorded_gitignore_pattern(self, tmp_path: pathlib.Path) -> None:
        """`.gitignore`へ記録済みの管理パターンとユーザーの変更を、commitも削除もしない。"""
        home = tmp_path / "home"
        root = home / "private-notes"
        git_repository.init_repository(root)
        (root / ".gitignore").write_text(f"tracked\n{_file_lock.PLAN_LOCK_IGNORE_PATTERN}\n", encoding="utf-8")
        git_repository.commit_all(root, "base")
        original_head = git_repository.git_output(root, "rev-parse", "HEAD")
        (root / ".gitignore").write_text(
            f"tracked\nuser-change\n{_file_lock.PLAN_LOCK_IGNORE_PATTERN}\n",
            encoding="utf-8",
        )

        assert _wi_sync.ensure_environment(home) == root  # pylint: disable=protected-access  # noqa: SLF001
        assert git_repository.git_output(root, "rev-parse", "HEAD") == original_head
        assert (root / ".gitignore").read_text(encoding="utf-8") == (
            f"tracked\nuser-change\n{_file_lock.PLAN_LOCK_IGNORE_PATTERN}\n"
        )


class TestMigrateLegacyLayout:
    """旧2階層レイアウトから平坦レイアウトへの自動移行を検証する。

    管理repoのパスはconftestが適用する隔離（`agent_toolkit._testing.isolation`）が`tmp_path/private-notes`へ差し替えるため、
    `ensure_environment`へ渡すhomeは解決結果に影響しない。
    """

    @pytest.mark.parametrize(
        "filename",
        [
            "20260101-000000-001.md",
            "日本語 space.md",
            pytest.param(
                "日本語 space\n改行.md",
                marks=pytest.mark.skipif(os.name == "nt", reason="Windowsのファイル名は改行を許可しない"),
            ),
        ],
    )
    def test_migrates_entries_and_removes_legacy_dirs(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], filename: str
    ) -> None:
        """種別ディレクトリ配下のエントリへtypeを補って状態ディレクトリ直下へ移し、旧ディレクトリを削除する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                f"feedback/inbox/{filename}": _LEGACY_AWI,
                f"feedback/adopted/done-{filename}": _LEGACY_AWI,
                "tbd/inbox/20260102-000000-001.md": _LEGACY_UWI,
            },
        )

        assert _wi_sync.ensure_environment(tmp_path) == root  # pylint: disable=protected-access  # noqa: SLF001

        assert not (root / "feedback").exists()
        assert not (root / "tbd").exists()
        first = _wi_entries.entry_path(root, filename)
        parsed = _frontmatter.parse_frontmatter(first.read_text(encoding="utf-8"))
        assert parsed == ({"target_repo": "github.com/example/repo", "type": "awi", "state": "inbox"}, "\n本文\n")
        done = _wi_entries.entry_path(root, f"done-{filename}")
        assert _wi_entries.entry_state_of(done) == "adopted"
        second = _wi_entries.entry_path(root, "20260102-000000-001.md")
        parsed_second = _frontmatter.parse_frontmatter(second.read_text(encoding="utf-8"))
        assert parsed_second is not None
        assert parsed_second[0] == {
            "target_repo": "github.com/example/repo",
            "type": "uwi",
            "question_type": "free-form",
            "state": "inbox",
        }
        assert "3件を平坦レイアウトへ移行" in capsys.readouterr().err
        assert not git_repository.git_output(root, "status", "--porcelain")

    def test_is_noop_after_migration(self, tmp_path: pathlib.Path) -> None:
        """移行後の再実行では追加のコミットを生成しない。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(root, {"feedback/inbox/20260101-000000-001.md": _LEGACY_AWI})
        _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        head = git_repository.git_output(root, "rev-parse", "HEAD")

        _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert git_repository.git_output(root, "rev-parse", "HEAD") == head

    def test_startup_migration_does_not_reacquire_held_repo_lock(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """起動時のpullが同じキューの移行へ到達しても同じロックを再取得しない。"""
        root = tmp_path / "private-notes"
        filename = "20260101-000000-001.md"
        _init_legacy_repo(root, {f"inbox/{filename}": _LEGACY_AWI.replace("---\n\n", "type: awi\n---\n\n")})
        original_lock = _wi_sync.repo_lock

        def bounded_lock(path: pathlib.Path, *, timeout: float = -1) -> filelock.FileLock:
            del timeout
            return original_lock(path, timeout=0.2)

        monkeypatch.setattr(_wi_sync, "repo_lock", bounded_lock)
        _wi_sync.ensure_environment(tmp_path)
        assert _wi_entries.entry_state_of(_wi_entries.entry_path(root, filename)) == "inbox"
        assert git_repository.git_output(root, "rev-list", "--count", "HEAD") == "2"

    def test_removes_empty_legacy_dirs_without_commit(self, tmp_path: pathlib.Path) -> None:
        """エントリを含まない旧ディレクトリだけがある場合は削除のみで完結する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root, {"2026/01/20260101-000000-001.md": "---\ntarget_repo: r\ntype: awi\nstate: inbox\n---\n\n本文\n"}
        )
        (root / "feedback" / "inbox").mkdir(parents=True)
        head = git_repository.git_output(root, "rev-parse", "HEAD")

        _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert not (root / "feedback").exists()
        assert git_repository.git_output(root, "rev-parse", "HEAD") == head
        assert not git_repository.git_output(root, "status", "--porcelain")

    def test_aborts_without_changes_when_entry_is_broken(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """frontmatterが不正なエントリがある場合、何も移さずexit 2で原因を案内する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                "feedback/inbox/20260101-000000-001.md": _LEGACY_AWI,
                "feedback/inbox/20260101-000000-002.md": "frontmatterのない本文\n",
            },
        )

        with pytest.raises(SystemExit) as excinfo:
            _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert excinfo.value.code == 2
        assert "frontmatterが不正" in capsys.readouterr().err
        assert (root / "feedback" / "inbox" / "20260101-000000-001.md").exists()
        assert not (root / "inbox").exists()

    def test_aborts_when_destination_conflicts(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """種別違いで同名のエントリがある場合は移行先衝突として中止する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                "feedback/inbox/20260101-000000-001.md": _LEGACY_AWI,
                "tbd/inbox/20260101-000000-001.md": _LEGACY_UWI,
            },
        )

        with pytest.raises(SystemExit) as excinfo:
            _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert excinfo.value.code == 2
        assert "移行先が既に存在" in capsys.readouterr().err
