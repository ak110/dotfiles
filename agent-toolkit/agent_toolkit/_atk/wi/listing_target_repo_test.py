# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのlisting: target_repoのテスト。"""

import os
import pathlib
import shutil
import subprocess
import sys
import unicodedata

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.git_fakes import make_current_worktree_fake as _make_current_worktree_fake
from agent_toolkit._testing.git_fakes import make_git_remote_fake as _make_git_remote_fake
from agent_toolkit._testing.git_fakes import make_outside_worktree_fake as _make_outside_worktree_fake
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _FIXED_TIMESTAMP, _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


class TestListTargetRepoFilter:
    """listサブコマンド: --target-repo指定で一致するエントリのみ出力する。"""

    def test_filter_matches_single_group(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数target_repo混在でも--target-repo指定値と一致するエントリのみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--target-repo=github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "github.com/example/foo" in captured.out
        assert "github.com/example/bar" not in captured.out

    def test_filter_matches_legacy_path_and_url_forms(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """ローカルパス指定は旧パス形と現行URL形を同じ対象集合として列挙する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_awi_file(notes, "legacy.md", target_repo=str(local_repo))
        _write_awi_file(notes, "current.md", target_repo="github.com/example/myrepo")
        _write_awi_file(notes, "missing.md", target_repo=str(tmp_path / "missing"))
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", f"--target-repo={local_repo}", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "legacy.md" in output
        assert "current.md" in output
        assert "missing.md" not in output

    def test_filter_expands_tilde(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """~プレフィックスのローカルパスがgit remote get-urlで正規化され、対応するエントリが出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/myrepo")
        monkeypatch.setenv("HOME", str(tmp_path))

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--target-repo=~/myrepo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# awi\nfb-001.md: github.com/example/myrepo [inbox/normal/ready] テスト本文\n"

    def test_filter_no_match_outputs_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """一致するエントリが存在しない場合、標準出力は空になる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--target-repo=github.com/example/nomatch"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListTargetRepoDefault:
    """listサブコマンド: --target-repo未指定時はカレントディレクトリのリポジトリを対象とする。"""

    def test_default_limits_to_current_repository(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未指定時はカレントディレクトリが属するリポジトリのエントリだけを出力する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        _write_awi_file(notes, "current.md", target_repo="github.com/example/myrepo")
        _write_awi_file(notes, "other.md", target_repo="github.com/example/other")
        monkeypatch.setattr(subprocess, "run", _make_current_worktree_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "current.md" in output
        assert "other.md" not in output

    def test_all_value_covers_every_repository(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--target-repo=all`はカレントディレクトリのリポジトリによらず全件を出力する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        _write_awi_file(notes, "current.md", target_repo="github.com/example/myrepo")
        _write_awi_file(notes, "other.md", target_repo="github.com/example/other")
        monkeypatch.setattr(subprocess, "run", _make_current_worktree_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull", "--target-repo=all"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "current.md" in output
        assert "other.md" in output

    def test_outside_git_worktree_covers_every_repository(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Gitの作業ツリー外では対象を限定せず全件を出力する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "current.md", target_repo="github.com/example/myrepo")
        _write_awi_file(notes, "other.md", target_repo="github.com/example/other")
        monkeypatch.setattr(subprocess, "run", _make_outside_worktree_fake())

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "current.md" in output
        assert "other.md" in output

    def test_single_target_subcommand_rejects_all_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """単一の対象リポジトリを確定するサブコマンドは`all`を受理せず終了コード2で終わる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "current.md", target_repo="github.com/example/myrepo")
        monkeypatch.setattr(subprocess, "run", _make_outside_worktree_fake())

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "hold", "current.md", "--target-repo=all"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "--target-repo=all" in capsys.readouterr().err


class TestListNonTtyTargetRepo:
    """listサブコマンド: 非TTYでは対象repoと要約を短縮しないことを検証する。"""

    _LONG_REPO = "github.com/organization-name/very-long-repository-name-example"

    @staticmethod
    def _display_width(text: str) -> int:
        """東アジア文字幅に基づく出力文字列の表示幅を返す。"""
        return sum(2 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 1 for char in text)

    def test_awi_non_tty_preserves_full_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非TTYのAWI行はtarget_repoを完全に保持する。"""
        terminal_columns = 70

        def get_terminal_size(*args: object, **kwargs: object) -> os.terminal_size:
            del args, kwargs
            return os.terminal_size((terminal_columns, 24))

        notes = _setup_notes(tmp_path)
        body = "本文" * 80
        _write_awi_file(notes, "fb-001.md", target_repo=self._LONG_REPO, body=body)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(shutil, "get_terminal_size", get_terminal_size)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()
        assert self._LONG_REPO in captured.out
        assert body in captured.out
        assert any(self._display_width(line) > terminal_columns for line in output_lines)

    def test_awi_tty_shortens_to_terminal_width(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """TTYだけはtarget_repoと要約を端末幅へ短縮する。"""
        terminal_columns = 70
        notes = _setup_notes(tmp_path)
        body = "本文" * 80
        _write_awi_file(notes, "fb-001.md", target_repo=self._LONG_REPO, body=body)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        def get_terminal_size(*args: object, **kwargs: object) -> os.terminal_size:
            del args, kwargs
            return os.terminal_size((terminal_columns, 24))

        monkeypatch.setattr(shutil, "get_terminal_size", get_terminal_size)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        output_lines = capsys.readouterr().out.splitlines()
        assert self._LONG_REPO not in output_lines[-1]
        assert self._display_width(output_lines[-1]) <= terminal_columns

    def test_uwi_non_tty_preserves_full_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非TTYのUWI行はtarget_repoを完全に保持する。"""
        terminal_columns = 70

        def get_terminal_size(*args: object, **kwargs: object) -> os.terminal_size:
            del args, kwargs
            return os.terminal_size((terminal_columns, 24))

        notes = _setup_notes(tmp_path)
        _write_uwi_file(
            notes,
            f"{_FIXED_TIMESTAMP}-001.md",
            question="q1",
            answer="",
            target_repo=self._LONG_REPO,
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(shutil, "get_terminal_size", get_terminal_size)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=no"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()
        assert self._LONG_REPO in captured.out
        assert any(self._display_width(line) > terminal_columns for line in output_lines)
