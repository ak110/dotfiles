"""atk (agent-toolkit `atk fb`) のshowサブコマンドのテスト。

FILENAME指定表示・--all全件表示・型フィルター・状態フィルター・
--include-processed（adopted・rejected配下探索）・--skip-pullの単体テストを集約する。
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
        assert "### fb-001.md [inbox]" in captured.out
        assert "本文1" in captured.out
        assert "## target_repo: github.com/example/bar" in captured.out
        assert "### fb-002.md [inbox]" in captured.out
        assert "本文2" in captured.out


class TestShowStatusAll:
    """showサブコマンド `--all --status=all`: 全状態（adopted・rejected含む）を出力する。"""

    def test_all_status_all_includes_every_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--all --status=all`指定時、inbox・processing・adopted・rejectedの全件が状態ラベル付きで出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", target_repo="github.com/example/foo", body="inbox本文")
        adopted_dir = notes / "feedback" / "adopted"
        adopted_dir.mkdir(parents=True, exist_ok=True)
        (adopted_dir / "fb-adopted.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nadopted本文\n",
            encoding="utf-8",
        )
        rejected_dir = notes / "feedback" / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        (rejected_dir / "fb-rejected.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nrejected本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "--all", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-inbox.md [inbox]" in captured.out
        assert "### fb-adopted.md [adopted]" in captured.out
        assert "### fb-rejected.md [rejected]" in captured.out


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
        assert "usage: atk fb show" in captured.err
        assert "FILENAME" in captured.err
        assert "--all" in captured.err
        error_line = captured.err.rstrip("\n").splitlines()[-1]
        assert "表示するファイル名または--allを指定してください" in error_line


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
    """showサブコマンド: --statusでtbd側エントリのみ回答状況を限定する。"""

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


def _write_feedback_state_file(
    notes: pathlib.Path,
    state: str,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "state本文",
) -> pathlib.Path:
    """feedback/<state>配下（processing・adopted・rejected等）に1ファイルを書き込み、絶対パスを返す。"""
    state_dir = notes / "feedback" / state
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / filename
    path.write_text(
        f"---\ntarget_repo: {target_repo}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _write_feedback_processing_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "processing本文",
) -> pathlib.Path:
    """feedback/processing配下に1ファイルを書き込み、絶対パスを返す（`_write_feedback_state_file`の薄いラッパー）。"""
    return _write_feedback_state_file(notes, "processing", filename, target_repo=target_repo, body=body)


class TestShowProcessing:
    """showサブコマンド: processing状態も探索・走査対象に含める。"""

    def test_single_file_finds_entry_in_processing_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定時にinboxで見つからずprocessingで見つかる場合、当該本文が表示される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_processing_file(notes, "fb-processing.md", body="processing本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "fb-processing.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-processing.md" in captured.out
        assert "processing本文" in captured.out

    def test_single_file_inbox_precedes_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定時、inbox→processingの順で探索しinbox側が優先される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-dup.md", body="inbox側本文")
        _write_feedback_processing_file(notes, "fb-dup.md", body="processing側本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "fb-dup.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "inbox側本文" in captured.out
        assert "processing側本文" not in captured.out

    def test_all_scans_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all指定時にinboxとprocessingの双方の本文がグループ化されて出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="inbox本文")
        _write_feedback_processing_file(notes, "fb-processing.md", body="processing本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "--all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-inbox.md" in captured.out
        assert "inbox本文" in captured.out
        assert "### fb-processing.md" in captured.out
        assert "processing本文" in captured.out


class TestShowIncludeProcessed:
    """showサブコマンド: --include-processed指定時にFILENAME探索へadopted・rejectedを追加する。"""

    def test_include_processed_finds_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include-processed指定時にadopted配下のFILENAMEを参照できる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "fb-adopted.md", "--include-processed"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-adopted.md" in captured.out
        assert "adopted本文" in captured.out

    def test_include_processed_finds_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include-processed指定時にrejected配下のFILENAMEを参照できる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_state_file(notes, "rejected", "fb-rejected.md", body="rejected本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "fb-rejected.md", "--include-processed"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-rejected.md" in captured.out
        assert "rejected本文" in captured.out

    def test_include_processed_default_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include-processed未指定時はadopted配下のFILENAMEを渡してもexit 2になる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "fb-adopted.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox/tbdに存在しません" in captured.err

    def test_all_ignores_include_processed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--allモードは--include-processedの影響を受けずadopted・rejectedを含めない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="inbox本文")
        _write_feedback_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        _write_feedback_state_file(notes, "rejected", "fb-rejected.md", body="rejected本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "show", "--all", "--include-processed"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-inbox.md" in captured.out
        assert "adopted本文" not in captured.out
        assert "rejected本文" not in captured.out


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
