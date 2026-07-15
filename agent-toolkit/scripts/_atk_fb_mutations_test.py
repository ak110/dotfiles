"""atk (agent-toolkit `atk fb`) のadopt/reject/rm/edit・パストラバーサル検証のテスト。

adopt・reject・rm・editサブコマンドと、ファイル名引数の不正値拒否の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_fb_show_test.py`・
`_atk_fb_process_loop_test.py`に分離する。共通ヘルパーは`atk_test.py`から再利用する。
"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _GitCall,
    _make_subprocess_fake,
    _setup_flag_and_notes,
    _write_feedback_file,
)  # noqa: E402  # pylint: disable=wrong-import-position


class TestAdoptSingle:
    """adoptサブコマンド: 1件指定でinboxからadopted/へ移動しコミットを行う。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のadopt実行でinboxから移動されadopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "feedback" / "inbox" / "fb-001.md").exists()
        assert (notes / "feedback" / "adopted" / "fb-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 feedback item (adopted)" in commit_cmd


class TestAdoptMultiple:
    """adoptサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のadoptで全件がadopted/へ移動し単一コミットが行われること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        _write_feedback_file(notes, "fb-003.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md", "fb-002.md", "fb-003.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "feedback" / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        assert not (inbox / "fb-003.md").exists()
        adopted = notes / "feedback" / "adopted"
        assert (adopted / "fb-001.md").exists()
        assert (adopted / "fb-002.md").exists()
        assert (adopted / "fb-003.md").exists()

        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 3 feedback items (adopted)" in commit_cmds[0]


class TestAdoptZeroArgs:
    """adoptサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt"], home=tmp_path)

        assert exc_info.value.code == 2


class TestAdoptMissing:
    """adoptサブコマンド: 存在しないファイル指定でexit 2となる。"""

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
            atk.main(["fb", "adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestAdoptStampWithNoteAndCommit:
    """adopt: --note・--commit指定時に`## 処理結果`節へ全項目が追記される。"""

    def test_stamp_written_with_all_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note・--commit指定時、adopted/配下のファイル末尾に採否・処理日時・対応commit・メモが追記される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="元本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "adopt", "fb-001.md", "--note", "採用理由サマリー", "--commit", "abc1234"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        adopted_text = (notes / "feedback" / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: abc1234" in adopted_text
        assert "- メモ: 採用理由サマリー" in adopted_text


class TestAdoptStampWithCategory:
    """adopt: --category指定時に`## 処理結果`節へカテゴリが追記される。"""

    def test_stamp_written_with_category(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--category指定時、adopted/配下のファイル末尾にカテゴリ行が追記される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

        assert exc_info.value.code == 0
        adopted_text = (notes / "feedback" / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "- カテゴリ: scope-escalation" in adopted_text


class TestAdoptCategoryGate:
    """adopt: 同一カテゴリの採用件数が閾値へ到達した場合に警告を出力する。"""

    def test_below_threshold_has_no_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一カテゴリの採用件数が閾値未満の場合は標準エラー出力へ警告しない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "採用件数" not in captured.err

    def test_threshold_reached_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一カテゴリの採用件数が閾値へ到達した場合は標準エラー出力へ警告する。"""
        notes = _setup_flag_and_notes(tmp_path)
        adopted = notes / "feedback" / "adopted"
        adopted.mkdir(parents=True, exist_ok=True)
        for index in range(1, 3):
            (adopted / f"old-{index}.md").write_text(
                "---\ntarget_repo: github.com/example/foo\n---\n\n"
                "## 処理結果\n\n"
                "- 採否: adopted\n"
                "- カテゴリ: scope-escalation\n",
                encoding="utf-8",
            )
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "カテゴリ「scope-escalation」の採用件数が3件に到達した" in captured.err
        assert "上位カテゴリでの規範化・仕組み化" in captured.err


class TestAdoptStampWithoutOptional:
    """adopt: --note・--commit省略時も必須項目のみ追記される。"""

    def test_stamp_written_with_required_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """引数省略時、`## 処理結果`節に採否・処理日時のみ追記され、対応commit・メモ行は含まれない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        adopted_text = (notes / "feedback" / "adopted" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: " not in adopted_text
        assert "- メモ: " not in adopted_text
        assert "- カテゴリ: " not in adopted_text


class TestRejectDeletes:
    """rejectサブコマンド: ファイルをinboxからrejected/へ移動する。"""

    def test_single_file_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rejectでファイルがinboxから移動されrejected/に置かれコミット件名が正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "reject", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "feedback" / "inbox" / "fb-001.md").exists()
        assert (notes / "feedback" / "rejected" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 feedback item (rejected)" in commit_cmd


class TestRejectStampWithNote:
    """reject: --note指定時に`## 処理結果`節へメモが追記される。"""

    def test_reject_stamp_note_written(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note指定時、rejected/配下のファイル末尾に採否・処理日時・メモが追記される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "reject", "fb-001.md", "--note", "不採用理由"], home=tmp_path)

        assert exc_info.value.code == 0
        rejected_text = (notes / "feedback" / "rejected" / "fb-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in rejected_text
        assert "- 採否: rejected" in rejected_text
        assert "- メモ: 不採用理由" in rejected_text


class TestRejectMultiple:
    """rejectサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_rejected_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrejectで両方がrejected/へ移動し単一コミットが行われること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "reject", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "feedback" / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        rejected = notes / "feedback" / "rejected"
        assert (rejected / "fb-001.md").exists()
        assert (rejected / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 2 feedback items (rejected)" in commit_cmds[0]


class TestRejectZeroArgs:
    """rejectサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "reject"], home=tmp_path)

        assert exc_info.value.code == 2


class TestRmSingle:
    """rmサブコマンド: 単純削除とコミット件名を検証する。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rmで対象ファイルが削除されコミット件名が正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "feedback" / "inbox" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 feedback item" in commit_cmd


class TestRmMultiple:
    """rmサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_removed_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrmで両方削除と単一コミットが行われること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "rm", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 feedback items" in commit_cmds[0]


class TestEditNoEditor:
    """editサブコマンド: $EDITOR未設定でexit 1となる。"""

    def test_no_editor_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """$EDITORが未設定の場合はexit 1と案内が出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        monkeypatch.delenv("EDITOR", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "EDITOR" in captured.err


class TestEditWithChanges:
    """editサブコマンド: 編集後差分ありでcommit・push実行。"""

    def test_edit_with_changes_commits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """編集後にファイル差分があればコミット・pushが実行される。"""
        notes = _setup_flag_and_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                path.write_text("編集後\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd


class TestEditNoChanges:
    """editサブコマンド: 差分なしでcommitせず終了。"""

    def test_edit_no_changes_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集後にファイル差分がなければコミットされず案内のみ出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


class TestEditNoArg:
    """editサブコマンド: 無引数時はinbox配下のファイル名順最大値（最終追加分）を対象とする。"""

    def test_edit_no_arg_selects_max_filename(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル存在時はファイル名順の最大値（最終追加分）が編集対象になる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "20240101-100000-001.md", body="旧")
        latest = _write_feedback_file(notes, "20240201-100000-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                assert cmd[1] == str(latest)
                latest.write_text("編集後\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "edit"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd

    def test_edit_no_arg_exits_on_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空の場合はexit 2でstderr案内を出力する。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "edit"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox" in captured.err


class TestStartProcessingSingle:
    """start-processingサブコマンド: 1件指定でinboxからprocessing/へ移動しコミットする。"""

    def test_single_file_moved_to_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のstart-processing実行でinboxから移動されprocessing/に置かれコミット件名が正しいこと。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "start-processing", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "feedback" / "inbox" / "fb-001.md").exists()
        assert (notes / "feedback" / "processing" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: start processing 1 feedback item" in commit_cmd


class TestStartProcessingMultiple:
    """start-processingサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_moved_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のstart-processingで両方がprocessing/へ移動し単一コミットが行われること。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "start-processing", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        processing = notes / "feedback" / "processing"
        assert (processing / "fb-001.md").exists()
        assert (processing / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: start processing 2 feedback items" in commit_cmds[0]


class TestStartProcessingMissing:
    """start-processingサブコマンド: 存在しないファイル指定でexit 2となる。"""

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
            atk.main(["fb", "start-processing", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inboxに存在しません" in captured.err


class TestAdoptFromProcessing:
    """adopt: processing配下のファイルもadopted/へ移動できる。"""

    def test_adopt_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがadopt対象に含まれadopted/へ移動する。"""
        notes = _setup_flag_and_notes(tmp_path)
        processing = notes / "feedback" / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "feedback" / "adopted" / "fb-p.md").exists()


class TestRejectFromProcessing:
    """reject: processing配下のファイルもrejected/へ移動できる。"""

    def test_reject_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがreject対象に含まれrejected/へ移動する。"""
        notes = _setup_flag_and_notes(tmp_path)
        processing = notes / "feedback" / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "reject", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "feedback" / "rejected" / "fb-p.md").exists()


class TestProcessingPrecedence:
    """同名ファイルがinbox・processing双方に存在する場合processingを優先する。"""

    def test_adopt_prefers_processing_when_both_exist(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """同名ファイルがinbox・processing双方に存在する場合、processing側が移動元として選ばれる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-dup.md")
        inbox_path = notes / "feedback" / "inbox" / "fb-dup.md"
        inbox_path.write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\ninbox本文\n",
            encoding="utf-8",
        )
        processing = notes / "feedback" / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-dup.md"], home=tmp_path)

        assert exc_info.value.code == 0
        # processing側が移動元として選ばれるため、inbox側は残存しprocessing側は消える。
        assert inbox_path.exists()
        assert not processing_path.exists()
        adopted_path = notes / "feedback" / "adopted" / "fb-dup.md"
        assert adopted_path.exists()
        # 実際に移動されたのはprocessing側の内容であることを確認する。
        assert "processing本文" in adopted_path.read_text(encoding="utf-8")


class TestTargetRepoVerification:
    """mutation系サブコマンド: `--target-repo`指定時のfrontmatter一致検証を検証する。

    既定のfrontmatter`target_repo`は`github.com/example/foo`（`_write_feedback_file`既定値）。
    """

    def test_adopt_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """adopt: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "target_repo不一致" in captured.err
        assert (notes / "feedback" / "inbox" / "fb-001.md").exists()
        assert not (notes / "feedback" / "adopted" / "fb-001.md").exists()

    def test_adopt_match_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: `--target-repo`一致時は通常通りadopted/へ移動する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md", "--target-repo", "github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "feedback" / "adopted" / "fb-001.md").exists()

    def test_reject_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """reject: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "reject", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "feedback" / "inbox" / "fb-001.md").exists()

    def test_rm_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rm: `--target-repo`不一致時にexit 2でファイルは削除されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "rm", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "feedback" / "inbox" / "fb-001.md").exists()

    def test_start_processing_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "start-processing", "fb-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "feedback" / "inbox" / "fb-001.md").exists()

    def test_edit_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """edit: `--target-repo`不一致時にexit 2でエディターは起動されない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "edit", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert not editor_calls

    def test_unspecified_target_repo_is_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--target-repo`未指定時は検証されず既存挙動のまま処理が進む。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "feedback" / "adopted" / "fb-001.md").exists()


class TestPathTraversalRejection:
    """パストラバーサル系の不正引数は早期に拒否されること。"""

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape.md",
            "subdir/file.md",
            "/abs/path.md",
            "..\\windows.md",
            "..",
            ".",
            "",
        ],
    )
    def test_rejects_bad_filenames(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        bad: str,
    ) -> None:
        """不正なファイル名引数はexit 2でstderr案内を出力する。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "adopt", bad], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err
