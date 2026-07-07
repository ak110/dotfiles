"""atk (agent-toolkit `atk fb`) のshowサブコマンドのテスト。

FILENAME指定表示・--all全件表示・型フィルター・状態フィルター・--skip-pullの単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_fb_mutations_test.py`・
`_atk_fb_process_loop_test.py`に分離する。共通ヘルパーは`atk_test.py`から再利用する。
"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_flag_and_notes,
    _write_feedback_file,
    _write_tbd_file,
)  # noqa: E402  # pylint: disable=wrong-import-position


class TestShowSingleFile:
    """showサブコマンド: FILENAME指定で当該1件の本文のみを表示する。"""

    def test_single_file_shows_only_that_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定時は当該1件のtarget_repoグループ・ファイル名・本文が出力され他件は出力されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar", body="本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "## target_repo: github.com/example/foo" in captured.out
        assert "### fb-001.md" in captured.out
        assert "本文1" in captured.out
        assert "fb-002.md" not in captured.out
        assert "本文2" not in captured.out

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox/tbdに存在しません" in captured.err

    def test_target_repo_mismatch_falls_through_and_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定と--target-repo不一致時は次のkindへ進み、全kind該当なしでexit 2となる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "show", "fb-001.md", "--target-repo=github.com/example/bar"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "inbox/tbdに存在しません" in captured.err


class TestShowAll:
    """showサブコマンド: --all指定でtarget_repoごとにグループ化した全件本文を表示する。"""

    def test_all_shows_every_entry_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all指定時は複数target_repoの全件がグループ化されて出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar", body="本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "--all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "## target_repo: github.com/example/foo" in captured.out
        assert "### fb-001.md" in captured.out
        assert "本文1" in captured.out
        assert "## target_repo: github.com/example/bar" in captured.out
        assert "### fb-002.md" in captured.out
        assert "本文2" in captured.out


class TestShowRequiresFilenameOrAll:
    """showサブコマンド: FILENAME・--allのいずれも未指定の場合はエラー終了する。"""

    def test_neither_specified_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME・--allともに未指定の場合はexit 2で案内が出力される。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "FILENAME" in captured.err
        assert "--all" in captured.err


class TestShowTypeFilter:
    """showサブコマンド: --typeでFILENAME探索対象inboxを限定する。"""

    def test_type_tbd_finds_tbd_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=tbd指定時はtbd/inboxのみを探索しstatusラベル付きで出力する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", f"{_FIXED_TIMESTAMP}-001.md", "--type=tbd"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"### {_FIXED_TIMESTAMP}-001.md [unanswered]" in captured.out

    def test_type_feedback_excludes_tbd_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=feedback指定時はtbd/inboxを探索せず該当なしでexit 2になる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", f"{_FIXED_TIMESTAMP}-001.md", "--type=feedback"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox/tbdに存在しません" in captured.err

    def test_type_all_searches_feedback_then_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=all（既定）はfeedback/inbox→tbd/inboxの順で探索し先に見つかった方を表示する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"### {_FIXED_TIMESTAMP}-001.md [unanswered]" in captured.out


class TestShowStatusFilter:
    """showサブコマンド: --statusでtbd側エントリのみ回答状況を絞り込む。"""

    def test_all_status_answered_excludes_unanswered_tbd_but_keeps_feedback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all --status=answeredはtbd側の未回答を除外し、feedback側には作用しない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "--all", "--status=answered"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-001.md" in captured.out
        assert f"### {_FIXED_TIMESTAMP}-002.md [answered]" in captured.out
        assert f"{_FIXED_TIMESTAMP}-001.md" not in captured.out

    def test_filename_status_mismatch_treated_as_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定時、対象tbdの回答状況が--statusと不一致なら未検出扱いでexit 2になる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "show", f"{_FIXED_TIMESTAMP}-001.md", "--status=answered"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox/tbdに存在しません" in captured.err


class TestShowSkipPull:
    """showサブコマンド: --skip-pull指定時はgit pullをスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はgit pull --ff-onlyが実行されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "--all", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] == ["git", "pull"] for c in git_calls)
