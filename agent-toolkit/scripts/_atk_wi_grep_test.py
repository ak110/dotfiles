"""atk (agent-toolkit `atk wi`) のgrepサブコマンドのテスト。

本文全体（frontmatter含む）の正規表現検索・大文字小文字無視・各種フィルター
（target-repo・type・status・answered）・該当0件時のexit 1・不正な正規表現時のexit 2の
単体テストを集約する。共通ヘルパーは`atk_test.py`から再利用する。
"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position
from _atk_git_fake_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    make_git_remote_fake as _make_git_remote_fake,
)

# pylint: disable-next=wrong-import-position,import-error
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
    _write_tbd_file,
)  # noqa: E402  # pylint: disable=wrong-import-position


class TestGrepBasic:
    """grepサブコマンド: 複数エントリ・複数行にまたがるマッチを検索する。"""

    def test_grep_finds_match_in_single_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """単一エントリ内の複数行マッチを`<ファイル名>:<行番号>:<該当行>`形式で出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="line1\nline2\nline3")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "line2", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-001.md:7:line2" in captured.out
        assert not any(call["cmd"][:2] in (["git", "fetch"], ["git", "merge"], ["git", "rebase"]) for call in git_calls)

    def test_grep_finds_no_match_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """該当0件時にexit 1で終了し標準出力が空であること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="line1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "nonexistent", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_recent_sync_is_reused_without_remote_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """直近の同期形跡がある通常検索ではremote同期を省略して再利用を案内する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="searchword")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(call["cmd"][:2] in (["git", "fetch"], ["git", "merge"]) for call in git_calls)
        assert capsys.readouterr().err == (
            "注記: 直近30秒に他プロセスを含む同期形跡があるため、直近の同期結果を再利用しました。"
            "最新化する場合は`--pull`を指定してください。\n"
        )

    def test_pull_forces_remote_sync_after_recent_sync(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--pull指定時は直近の同期形跡があってもfetch・mergeを実行する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="searchword")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--pull"], home=tmp_path)

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
            atk.main(["wi", "grep", ".", "--skip-pull", "--pull"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "not allowed with argument" in capsys.readouterr().err


class TestGrepIgnoreCase:
    """grepサブコマンド: --ignore-caseで大文字小文字を無視する。"""

    def test_ignore_case_matches_different_case(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--ignore-case指定時に大文字小文字を無視して一致すること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="Uppercase")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "uppercase", "-i", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.out

    def test_case_sensitive_differs_by_case(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--ignore-case未指定時は大文字小文字を区別すること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="Uppercase")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "uppercase", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""


class TestGrepFilters:
    """grepサブコマンド: 各フィルターが`list`と同じ意味で作用すること。"""

    def test_type_filter_limits_to_feedback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--type=feedback`でフィードバック種別のみを対象とする。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="searchword")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="searchword")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--type=feedback", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.out
        assert f"{_FIXED_TIMESTAMP}-001.md" not in captured.out

    def test_target_repo_filter_matches_legacy_local_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """grepも旧パス形と現行URL形を同じ対象として検索する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_feedback_file(notes, "legacy.md", target_repo=str(local_repo), body="searchword")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "grep", "searchword", "--target-repo=github.com/example/myrepo", "--skip-pull"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert "legacy.md" in capsys.readouterr().out

    def test_type_filter_limits_to_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=tbdでtbd種別のみを対象とする。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="searchword")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="searchword")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--type=tbd", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-001.md" not in captured.out
        assert f"{_FIXED_TIMESTAMP}-001.md" in captured.out

    def test_active_includes_hold_entries_of_both_types(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """既定activeのgrepはhold配下のfeedbackとTBDをいずれも含める。"""
        notes = _setup_notes(tmp_path)
        hold_dir = notes / "hold"
        hold_dir.mkdir(parents=True, exist_ok=True)
        (hold_dir / "hold-feedback.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\nsearchword\n",
            encoding="utf-8",
        )
        (hold_dir / "hold-tbd.md").write_text(
            "---\ntype: tbd\ntarget_repo: github.com/example/foo\n---\n\nsearchword\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "hold-feedback.md" in captured.out
        assert "hold-tbd.md" in captured.out

    def test_answered_filter_limits_to_unanswered(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--answered=noで未回答TBDのみを対象とする。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="searchword", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="other", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--type=tbd", "--answered=no", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md" in captured.out
        assert f"{_FIXED_TIMESTAMP}-002.md" not in captured.out


class TestGrepFrontmatter:
    """grepサブコマンド: frontmatter部分（target_repo:等の行）も検索対象に含まれること。"""

    def test_grep_finds_match_in_frontmatter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """frontmatter内のtarget_repoフィールドも検索対象に含まれること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/searchword", body="body")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.out


class TestGrepInvalidRegex:
    """grepサブコマンド: 不正な正規表現指定時にexit 2で終了する。"""

    def test_invalid_regex_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """不正な正規表現指定時にexit 2で終了しエラーメッセージを標準エラーへ出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="text")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "[invalid(regex", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "正規表現が不正です" in captured.err
