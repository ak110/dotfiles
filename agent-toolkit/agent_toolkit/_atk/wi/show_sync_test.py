"""WIのshow: syncのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit.atk_test import _GitCall, _make_subprocess_fake, _setup_notes, _write_awi_file


class TestShowSkipPull:
    """showサブコマンド: --skip-pull指定時はremote同期全体をスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はfetch・merge・rebaseが実行されない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] in (["git", "fetch"], ["git", "merge"], ["git", "rebase"]) for c in git_calls)

    def test_recent_sync_is_reused_without_remote_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """直近の同期形跡がある通常表示ではremote同期を省略し、注記を付けない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(call["cmd"][:2] in (["git", "fetch"], ["git", "merge"]) for call in git_calls)
        assert not capsys.readouterr().err

    def test_pull_forces_remote_sync_after_recent_sync(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--pull指定時は直近の同期形跡があってもfetch・mergeを実行する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-001.md", "--pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert any(call["cmd"][:2] == ["git", "fetch"] for call in git_calls)
        assert any(call["cmd"][:2] == ["git", "merge"] for call in git_calls)

    def test_skip_pull_and_pull_are_mutually_exclusive(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--skip-pullと--pullの同時指定は終了コード2で拒否する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all", "--skip-pull", "--pull"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "not allowed with argument" in capsys.readouterr().err


class TestShowStatePrefixedFilename:
    """showサブコマンド: 状態名付きファイル名は再実行方法を案内して終了する。"""

    @pytest.mark.parametrize("state", ["inbox", "processing", "hold", "adopted", "rejected"])
    def test_state_prefix_reports_hint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        state: str,
    ) -> None:
        """既知の状態名で始まる入力は、状態名を除いた再実行方法を案内する。"""
        _setup_notes(tmp_path)
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        # 対象リポジトリを明示し、`--target-repo`省略時の解決によるgit呼び出しを検証対象から外す。
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", f"{state}/fb-001.md", "--target-repo=all"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "状態名を除いたファイル名を指定する: fb-001.md" in captured.err
        assert not git_calls

    def test_unknown_prefix_keeps_common_validation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """状態名でない接頭辞は共通のファイル名検証で拒否する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "unknown/fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err

    def test_traversal_keeps_common_validation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """状態名の後にカレント参照が続く入力は共通のファイル名検証で拒否する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "inbox/.."], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err
