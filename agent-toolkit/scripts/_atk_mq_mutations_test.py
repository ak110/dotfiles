"""atk (agent-toolkit `atk mq`) のadopt/reject/rm/edit・パストラバーサル検証のテスト。

adopt・reject・rm・editサブコマンドと、ファイル名引数の不正値拒否の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_mq_list_test.py`・
`_atk_mq_show_test.py`・`_atk_mq_process_loop_test.py`に分離する。
位置引数の重複除去（FB7）テストは`too-many-lines`回避のため`_atk_mq_dedup_test.py`へ分離する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import contextlib
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_frontmatter as frontmatter_parser  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_mutations as mutations  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_schedule as schedule  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_tbd as tbd  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
)  # noqa: E402  # pylint: disable=wrong-import-position


def _write_tbd_entry(
    notes: pathlib.Path,
    filename: str,
    *,
    question: str = "変更前の質問",
    answer: str = "既存回答",
    frontmatter: str = "target_repo: github.com/example/foo\ntype: tbd\nquestion_type: free-form",
) -> pathlib.Path:
    """非対話edit用のTBDエントリを書き込む。"""
    path = notes / "inbox" / filename
    path.write_text(
        f"---\n{frontmatter}\n---\n\n"
        f"{tbd.QUESTION_HEADING}\n\n{question}\n\n"
        f"{tbd.ANSWER_HEADING}\n\n{tbd.ANSWER_MARKER}\n{answer}\n",
        encoding="utf-8",
    )
    return path


def test_add_empty_feedback_keeps_detailed_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実質空feedbackのCLI拒否案内に判定条件と対象先頭を含める。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "add", "--target-repo", "github.com/example/foo", "-"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "空文字・空白のみ・箇条書きマーカー単独文字" in captured.err
    assert "該当メッセージの先頭: -" in captured.err


def test_flat_feedback_operations_are_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平引数遷移が戻り値とファイル移動を一貫して反映する。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    filenames = mutations.transition_entries(
        notes,
        action="start-processing",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert (notes / "processing/entry.md").is_file()


def test_return_to_inbox_moves_processing_to_inbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """return-to-inboxがprocessingからinboxへ戻す。"""
    notes = _setup_notes(tmp_path)
    _write_feedback_file(notes, "entry.md")
    monkeypatch.setattr(mutations, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    mutations.transition_entries(notes, action="start-processing", filenames=["entry.md"], now=_FIXED_DT)
    filenames = mutations.transition_entries(
        notes,
        action="return-to-inbox",
        filenames=["entry.md"],
        now=_FIXED_DT,
    )
    assert filenames == ["entry.md"]
    assert (notes / "inbox/entry.md").is_file()
    assert not (notes / "processing/entry.md").exists()


def test_return_to_inbox_missing_file_reports_processing_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """return-to-inboxで未存在ファイルを指定するとprocessing側の状態名で案内する。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "return-to-inbox", "nonexistent.md"], home=tmp_path)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "processingに存在しません" in captured.err


class TestAdoptSingle:
    """adoptサブコマンド: 1件指定でinboxからadopted/へ移動しコミットを行う。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のadopt実行でinboxから移動されadopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "adopted" / "fb-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (adopted)" in commit_cmd

    def test_adopt_bare_stem_from_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """拡張子.md省略入力がinbox側の実体を解決してadoptedへ移動する（fb 20260721-164301-001反映）。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "20260721-160220-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            # 拡張子.mdを省略した引数でadoptを呼ぶ
            atk.main(["mq", "adopt", "20260721-160220-001"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "20260721-160220-001.md").exists()
        assert (notes / "adopted" / "20260721-160220-001.md").exists()


class TestAdoptMultiple:
    """adoptサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のadoptで全件がadopted/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        _write_feedback_file(notes, "fb-003.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "fb-002.md", "fb-003.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        assert not (inbox / "fb-003.md").exists()
        adopted = notes / "adopted"
        assert (adopted / "fb-001.md").exists()
        assert (adopted / "fb-002.md").exists()
        assert (adopted / "fb-003.md").exists()

        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 3 entries (adopted)" in commit_cmds[0]


class TestAdoptZeroArgs:
    """adoptサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt"], home=tmp_path)

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
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "nonexistent.md"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="元本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "adopt", "fb-001.md", "--note", "採用理由サマリー", "--commit", "abc1234"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        adopted = notes / "adopted"
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
            atk.main(["mq", "adopt", "fb-001.md", "--category", "scope-escalation"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / "fb-001.md").read_text(encoding="utf-8")
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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "rejected" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (rejected)" in commit_cmd


class TestRejectStampWithNote:
    """reject: --note指定時に`## 処理結果`節へメモが追記される。"""

    def test_reject_stamp_note_written(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note指定時、rejected/配下のファイル末尾に採否・処理日時・メモが追記される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "--note", "不採用理由"], home=tmp_path)

        assert exc_info.value.code == 0
        rejected_text = (notes / "rejected" / "fb-001.md").read_text(encoding="utf-8")
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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        inbox = notes / "inbox"
        assert not (inbox / "fb-001.md").exists()
        assert not (inbox / "fb-002.md").exists()
        rejected = notes / "rejected"
        assert (rejected / "fb-001.md").exists()
        assert (rejected / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: process 2 entries (rejected)" in commit_cmds[0]


class TestRejectZeroArgs:
    """rejectサブコマンド: ファイル名引数0件でexit 2となる（nargs="+"のargparse制約）。"""

    def test_no_args_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ファイル名引数なしでargparseがexit 2を返すこと。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject"], home=tmp_path)

        assert exc_info.value.code == 2


class TestRmSingle:
    """rmサブコマンド: 単純削除とコミット件名を検証する。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rmで対象ファイルが削除されコミット件名が正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 entry" in commit_cmd

    def test_processing_file_removed_with_force(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing後（processing配下）のファイルは`--force`指定時のみrm対象として解決される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nテスト本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "--force", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing_dir / "fb-001.md").exists()

    def test_processing_file_rejected_without_force(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--force`未指定時、processing配下のファイルは削除を拒否されexit 2する（フィードバック20260723-153526-001反映）。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\n---\n\nテスト本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (processing_dir / "fb-001.md").exists()
        captured = capsys.readouterr()
        assert "processing状態のファイルは既定で削除を保護します" in captured.err
        assert "fb-001.md" in captured.err

    def test_missing_file_reports_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processingいずれにも存在しない場合、両状態を明記したメッセージでexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-missing.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestRmMultiple:
    """rmサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_removed_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のrmで両方削除と単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 entries" in commit_cmds[0]


class TestEditNoEditor:
    """editサブコマンド: $EDITOR未設定でexit 1となる。"""

    def test_no_editor_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """$EDITORが未設定の場合はexit 1と案内が出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        monkeypatch.delenv("EDITOR", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                # 新設計ではエディターへ渡されるのは対象ファイルのスナップショットを
                # 複製した一時ファイルのため、元ファイルではなくcmd[1]を書き換える。
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(editor_path.read_text(encoding="utf-8").replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd

    def test_processing_file_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing後（processing配下）のファイルも編集対象として解決される。"""
        notes = _setup_notes(tmp_path)
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True)
        (processing_dir / "fb-001.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(editor_path.read_text(encoding="utf-8").replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (processing_dir / "fb-001.md").read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_editor_target_repo_change_invalidates_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """対話編集でtarget_repoを変更した場合は旧リポジトリのtarget_commitを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "type: feedback\n",
                f"type: feedback\ntarget_commit: {'a' * 40}\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("EDITOR", "fake-editor")

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_path = pathlib.Path(cmd[1])
                editor_path.write_text(
                    editor_path.read_text(encoding="utf-8").replace(
                        "target_repo: github.com/example/foo",
                        "target_repo: github.com/example/new",
                    ),
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_missing_file_reports_inbox_and_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processingいずれにも存在しない場合、両状態を明記したメッセージでexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-missing.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err


class TestEditNoChanges:
    """editサブコマンド: 差分なしでcommitせず終了。"""

    def test_edit_no_changes_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集後にファイル差分がなければコミットされず案内のみ出力される。"""
        notes = _setup_notes(tmp_path)
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
            atk.main(["mq", "edit", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert commit_cmds == []
        captured = capsys.readouterr()
        assert "差分なし" in captured.out


class TestNoninteractiveEdit:
    """editサブコマンドのMESSAGE指定による非対話編集を検証する。"""

    def test_feedback_body_updates_without_editor_and_preserves_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """EDITOR未設定でも本文を更新し、未指定メタデータを保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前", source="session-review")
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8") == (
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\nsource: session-review\n---\n\n編集後\n"
        )

    def test_message_does_not_start_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """MESSAGE指定時はEDITORが設定済みでもエディターを起動しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "must-not-run")
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
            if cmd[0] == "must-not-run":
                pytest.fail("MESSAGE指定時にEDITORが起動された")
            return _make_subprocess_fake(git_calls)(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0

    def test_target_repo_is_normalized_and_existing_frontmatter_lines_are_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """明示したtarget_repoを正規化し、他の意味的なキーを保持する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_text(
            "---\n# 保持するコメント\ntarget_repo: old.example/a/b\n\n"
            "target_repo: old.example/a/b\ntype: feedback\nsource: manual\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\ntarget_repo: https://github.com/Example/Repo.git\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0] == {
            "target_repo": "github.com/example/repo",
            "type": "feedback",
            "source": "manual",
        }
        assert parsed[1] == "\n編集後\n"

    @pytest.mark.parametrize(
        ("message", "exit_code", "error_fragment"),
        [
            ("---\ntype: tbd\n---\n\n本文", 2, "typeを変更"),
            ("---\nscope: item\n---\n\n本文", 1, "feedbackでは指定できない"),
            (" \n-\n ", 1, "実質空"),
        ],
    )
    def test_feedback_rejects_invalid_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
        exit_code: int,
        error_fragment: str,
    ) -> None:
        """種別変更・TBD専用キー・実質空本文を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == exit_code
        assert error_fragment in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_existing_file_path_is_rejected_without_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """実在ファイルパスだけのMESSAGEをtracebackなしで拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        message_file = tmp_path / "message.txt"
        message_file.write_text("編集後", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", str(message_file)], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ファイルパス" in captured.err
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n編集前\n")

    def test_empty_tbd_add_is_allowed_but_empty_edit_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空のTBD質問はaddで許容し、既存質問を削除するeditでは拒否する。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as add_exit:
            atk.main(
                [
                    "mq",
                    "add",
                    "--target-repo",
                    "github.com/example/foo",
                    "--type=tbd",
                    "--question-type=free-form",
                    "",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert add_exit.value.code == 0
        filename = f"{_FIXED_DT:%Y%m%d-%H%M%S}-001.md"
        assert (notes / "inbox" / filename).is_file()

        with pytest.raises(SystemExit) as edit_exit:
            atk.main(["mq", "edit", filename, ""], home=tmp_path)

        assert edit_exit.value.code == 1
        captured = capsys.readouterr()
        assert "質問本文は空にできません" in captured.err
        assert "Traceback" not in captured.err

    def test_processing_feedback_can_be_edited(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing配下のfeedbackも非対話で編集する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir()
        path = processing / "fb-001.md"
        path.write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\n編集前\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        assert path.read_text(encoding="utf-8").endswith("\n編集後\n")

    def test_tbd_question_and_scope_update_preserves_answer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """TBDの質問とscopeだけを更新し、回答領域を保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(
            notes,
            "tbd-001.md",
            frontmatter=("target_repo: github.com/example/foo\ntype: tbd\nscope: old\nquestion_type: choice\nchoices: A,B"),
        )
        original_answer = path.read_text(encoding="utf-8").split(tbd.ANSWER_HEADING, maxsplit=1)[1]
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nscope: new\n---\n\n変更後の質問"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", message], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert "scope: new" in content
        assert "変更後の質問" in content
        assert content.split(tbd.ANSWER_HEADING, maxsplit=1)[1] == original_answer

    @pytest.mark.parametrize(
        "message",
        [
            f"変更後\n\n{tbd.ANSWER_HEADING}\n",
            f"変更後\n\n{tbd.ANSWER_MARKER}\n",
            "---\nquestion_type: invalid\n---\n\n変更後",
            "---\nquestion_type: choice\nchoices:\n---\n\n変更後",
        ],
    )
    def test_tbd_rejects_invalid_question_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        message: str,
    ) -> None:
        """予約要素と不正な質問メタデータを拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(notes, "tbd-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert path.read_text(encoding="utf-8") == original

    def test_tbd_uses_last_answer_marker_and_preserves_answer_region(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """回答マーカー重複時も終端側を基準に質問だけを更新する。"""
        notes = _setup_notes(tmp_path)
        path = _write_tbd_entry(
            notes,
            "tbd-001.md",
            question=f"前半\n\n{tbd.ANSWER_MARKER}\n\n後半",
            answer="保持する回答",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "tbd-001.md", "変更後の質問"], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert content.count(tbd.ANSWER_MARKER) == 1
        assert content.endswith(f"{tbd.ANSWER_MARKER}\n保持する回答\n")

    def test_expected_content_conflict_keeps_message_unapplied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """競合時は上書きせず、FILENAMEと未反映を案内する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md", body="編集前")
        original_edit = mutations.edit_entry_content

        def conflict(
            private_notes: pathlib.Path,
            *,
            state: str,
            filename: str,
            content: str,
            target_repo: str | None = None,
            lock_timeout: float = -1,
            expected_content: str | None = None,
        ) -> bool:
            path.write_text(path.read_text(encoding="utf-8").replace("編集前", "競合側の変更"), encoding="utf-8")
            return original_edit(
                private_notes,
                state=state,
                filename=filename,
                content=content,
                target_repo=target_repo,
                lock_timeout=lock_timeout,
                expected_content=expected_content,
            )

        monkeypatch.setattr(mutations, "edit_entry_content", conflict)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.err
        assert "反映されていません" in captured.err
        assert path.read_text(encoding="utf-8").endswith("\n競合側の変更\n")

    def test_logically_identical_feedback_reports_no_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """論理本文が同一ならコミットせず差分なしを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "本文"], home=tmp_path)

        assert exc_info.value.code == 0
        assert "差分なし。" in capsys.readouterr().out
        assert not [call for call in git_calls if "commit" in call["cmd"]]

    def test_edit_rejects_explicit_queue_schedule(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """edit経路からqueue_scheduleを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nqueue_schedule:\n  type: normal\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_edit_rejects_explicit_target_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """非対話edit経路からtarget_commitを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\ntarget_commit: {'b' * 40}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize("updated_commit", ["b" * 40, None])
    def test_edit_content_validator_rejects_target_commit_change_or_removal(
        self,
        updated_commit: str | None,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """共通保存境界が同一リポジトリのtarget_commit変更と削除を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            f"type: feedback\ntarget_commit: {'a' * 40}\n",
        )
        path.write_text(original, encoding="utf-8")
        replacement = "" if updated_commit is None else f"target_commit: {updated_commit}\n"
        updated = original.replace(f"target_commit: {'a' * 40}\n", replacement)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(mutations.WebInputError):
            mutations.edit_entry_content(
                notes,
                state="inbox",
                filename="fb-001.md",
                content=updated,
                lock_timeout=2.0,
            )

        assert path.read_text(encoding="utf-8") == original

    def test_edit_content_boundary_invalidates_target_commit_on_target_repo_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Web API共通保存境界はtarget_repo変更時に旧target_commitを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            f"type: feedback\ntarget_commit: {'a' * 40}\n",
        )
        path.write_text(original, encoding="utf-8")
        updated = original.replace("target_repo: github.com/example/foo", "target_repo: github.com/example/new")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        assert mutations.edit_entry_content(
            notes,
            state="inbox",
            filename="fb-001.md",
            content=updated,
            lock_timeout=2.0,
        )

        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["target_repo"] == "github.com/example/new"
        assert "target_commit" not in parsed[0]

    def test_edit_rejects_explicit_plan_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """edit経路からplan_fileを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = "---\nplan_file: /tmp/plan.md\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    @pytest.mark.parametrize("updated_plan_file", ["/tmp/other.md", None])
    def test_edit_content_validator_rejects_plan_file_change_or_removal(
        self,
        updated_plan_file: str | None,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """共通保存境界が既存plan_fileの変更と削除を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            "type: feedback\nplan_file: /tmp/plan.md\n",
        )
        path.write_text(original, encoding="utf-8")
        replacement = "" if updated_plan_file is None else f"plan_file: {updated_plan_file}\n"
        updated = original.replace("plan_file: /tmp/plan.md\n", replacement)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(mutations.WebInputError):
            mutations.edit_entry_content(
                notes,
                state="inbox",
                filename="fb-001.md",
                content=updated,
                lock_timeout=2.0,
            )

        assert path.read_text(encoding="utf-8") == original

    def test_edit_preserves_plan_file_when_only_body_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """plan_fileを持つ項目も本文だけの編集を許容する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            "type: feedback\nplan_file: /tmp/plan.md\n",
        )
        path.write_text(original, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["plan_file"] == "/tmp/plan.md"

    def test_edit_content_validator_rejects_queue_schedule_via_direct_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """共通保存境界のcontent_validatorがqueue_scheduleの追加を拒否する（edit_entry_content直接呼び出し）。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original_content = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        # 元のfrontmatterにqueue_scheduleが無いことを確認
        original_parsed = frontmatter_parser.parse_frontmatter(original_content)
        assert original_parsed is not None
        original_data, _ = original_parsed
        assert "queue_schedule" not in original_data

        # queue_scheduleを追加した新しいcontentを作成
        new_content = (
            "---\ntarget_repo: github.com/example/repo\ntype: feedback\nqueue_schedule:\n  type: normal\n---\n\n編集後の本文\n"
        )

        # edit_entry_contentへ直接呼び出し。共通保存境界のvalidatorがqueue_schedule追加を拒否する
        with pytest.raises(mutations.WebInputError) as exc_info:
            mutations.edit_entry_content(
                notes,
                state="inbox",
                filename="fb-001.md",
                content=new_content,
                lock_timeout=2.0,
            )

        assert "予約キー" in str(exc_info.value)
        # ファイルは変更されていない
        assert path.read_text(encoding="utf-8") == original_content

    @pytest.mark.parametrize(
        ("reserved_key", "reserved_value"),
        [("repair_target", "broken.md"), ("repair_kind", "frontmatter")],
    )
    def test_edit_rejects_explicit_repair_metadata(
        self,
        reserved_key: str,
        reserved_value: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """edit経路から修復TBDの予約キーを注入できない。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        message = f"---\n{reserved_key}: {reserved_value}\n---\n\n編集後"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", message], home=tmp_path)

        assert exc_info.value.code == 1
        assert "予約キー" in capsys.readouterr().err
        assert path.read_text(encoding="utf-8") == original

    def test_edit_invalidates_queue_schedule_on_target_repo_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """target_repo変更時は旧リポジトリ向け分類メタデータを削除する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        text = path.read_text(encoding="utf-8")
        metadata = schedule.ScheduleMetadata(
            schedule.body_sha256(text),
            "github.com/example/foo",
            "normal",
            schedule.Dependency("none"),
            None,
            ("README.md",),
            0,
            (),
        )
        path.write_text(schedule.serialize_schedule_metadata(text, metadata), encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "edit",
                    "fb-001.md",
                    "---\ntarget_repo: github.com/example/new\n---\n\n編集後",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert "queue_schedule" not in parsed[0]

    def test_edit_preserves_queue_schedule_when_other_keys_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """target_repo以外のfrontmatter更新ではqueue_scheduleを保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        text = path.read_text(encoding="utf-8")
        metadata = schedule.ScheduleMetadata(
            schedule.body_sha256(text),
            "github.com/example/foo",
            "normal",
            schedule.Dependency("none"),
            None,
            ("README.md",),
            0,
            (),
        )
        path.write_text(schedule.serialize_schedule_metadata(text, metadata), encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "---\nsource: manual\n---\n\n本文"], home=tmp_path)

        assert exc_info.value.code == 0
        parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0]["source"] == "manual"
        assert "queue_schedule" in parsed[0]

    def test_edit_preserves_nested_queue_schedule_mapping_when_unrelated_key_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """無関係なキーの更新前後でqueue_scheduleの入れ子構造を保持する。"""
        notes = _setup_notes(tmp_path)
        path = _write_feedback_file(notes, "fb-001.md")
        text = path.read_text(encoding="utf-8")
        metadata = schedule.ScheduleMetadata(
            schedule.body_sha256(text),
            "github.com/example/foo",
            "normal",
            schedule.Dependency("entries", ("dependency.md",)),
            None,
            ("README.md", "src/example.py"),
            1,
            ("dependency-unmet",),
        )
        path.write_text(schedule.serialize_schedule_metadata(text, metadata), encoding="utf-8")
        before = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert before is not None
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "---\nsource: manual\n---\n\n本文"], home=tmp_path)

        assert exc_info.value.code == 0
        after = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert after is not None
        assert after[0]["queue_schedule"] == before[0]["queue_schedule"]

    def test_edit_raises_web_input_error_when_frontmatter_is_corrupt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """frontmatter全体が破損している場合は編集を拒否する。"""
        notes = _setup_notes(tmp_path)
        path = notes / "inbox" / "fb-001.md"
        path.write_text("---\ntarget_repo: [broken\n---\n本文\n", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "編集後"], home=tmp_path)

        assert exc_info.value.code == 1
        assert "frontmatterが破損" in capsys.readouterr().err


class TestEditNoArg:
    """editサブコマンド: 無引数時はinbox配下のファイル名順最大値（最終追加分）を対象とする。"""

    def test_edit_no_arg_selects_max_filename(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル存在時はファイル名順の最大値（最終追加分）が編集対象になる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "20240101-100000-001.md", body="旧")
        latest = _write_feedback_file(notes, "20240201-100000-001.md", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")

        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:  # pylint: disable=unused-argument
            if cmd[0] == "fake-editor":
                # 新設計ではエディターへ渡されるのは対象ファイルのスナップショットを
                # 複製した一時ファイルのため、元ファイルではなくcmd[1]を書き換える。
                # 対象選択（最終追加分）の検証はcommit対象の相対パスで行う。
                editor_path = pathlib.Path(cmd[1])
                content = editor_path.read_text(encoding="utf-8")
                editor_path.write_text(content.replace("編集前", "編集後"), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit"], home=tmp_path)

        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: edit feedback item" in commit_cmd
        add_cmd = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "add"]][0]
        assert str(latest.relative_to(notes)) in add_cmd

    def test_edit_no_arg_exits_on_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空の場合はexit 2でstderr案内を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / "fb-001.md").exists()
        assert (notes / "processing" / "fb-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: start processing 1 entry" in commit_cmd


class TestStartProcessingMultiple:
    """start-processingサブコマンド: 複数件指定で単一コミットへまとめる。"""

    def test_multiple_files_moved_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """2件のstart-processingで両方がprocessing/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "fb-001.md", "fb-002.md"], home=tmp_path)

        assert exc_info.value.code == 0
        processing = notes / "processing"
        assert (processing / "fb-001.md").exists()
        assert (processing / "fb-002.md").exists()
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: start processing 2 entries" in commit_cmds[0]


class TestStartProcessingMissing:
    """start-processingサブコマンド: 存在しないファイル指定でexit 2となる。"""

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "start-processing", "nonexistent.md"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "adopted" / "fb-p.md").exists()


class TestRejectFromProcessing:
    """reject: processing配下のファイルもrejected/へ移動できる。"""

    def test_reject_from_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """processing/配下のファイルがreject対象に含まれrejected/へ移動する。"""
        notes = _setup_notes(tmp_path)
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        (processing / "fb-p.md").write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-p.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (processing / "fb-p.md").exists()
        assert (notes / "rejected" / "fb-p.md").exists()


class TestProcessingPrecedence:
    """同名ファイルがinbox・processing双方に存在する場合processingを優先する。"""

    def test_adopt_prefers_processing_when_both_exist(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """同名ファイルがinbox・processing双方に存在する場合、processing側が移動元として選ばれる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-dup.md")
        inbox_path = notes / "inbox" / "fb-dup.md"
        inbox_path.write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\ninbox本文\n",
            encoding="utf-8",
        )
        processing = notes / "processing"
        processing.mkdir(parents=True, exist_ok=True)
        processing_path = processing / "fb-dup.md"
        processing_path.write_text(
            "---\ntype: feedback\ntarget_repo: github.com/example/foo\n---\n\nprocessing本文\n",
            encoding="utf-8",
        )
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-dup.md"], home=tmp_path)

        assert exc_info.value.code == 0
        # processing側が移動元として選ばれるため、inbox側は残存しprocessing側は消える。
        assert inbox_path.exists()
        assert not processing_path.exists()
        adopted_path = notes / "adopted" / "fb-dup.md"
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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "target_repo不一致" in captured.err
        assert (notes / "inbox" / "fb-001.md").exists()
        assert not (notes / "adopted" / "fb-001.md").exists()

    def test_adopt_match_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: `--target-repo`一致時は通常通りadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md", "--target-repo", "github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "adopted" / "fb-001.md").exists()

    def test_reject_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """reject: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "reject", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_rm_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """rm: `--target-repo`不一致時にexit 2でファイルは削除されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "rm", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_start_processing_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """start-processing: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "start-processing", "fb-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / "fb-001.md").exists()

    def test_edit_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """edit: `--target-repo`不一致時にexit 2でエディターは起動されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="編集前")
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "edit", "fb-001.md", "--target-repo", "github.com/other/repo"], home=tmp_path)

        assert exc_info.value.code == 2
        assert not editor_calls

    def test_unspecified_target_repo_is_noop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--target-repo`未指定時は検証されず既存挙動のまま処理が進む。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert (notes / "adopted" / "fb-001.md").exists()

    def test_adopt_bare_stem_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """adopt: 拡張子.md省略入力でも`--target-repo`不一致時に検証が回避されない（回帰確認）。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", "fb-001", "--target-repo", "github.com/other/repo"], home=tmp_path)
        assert exc_info.value.code == 2
        assert not (notes / "adopted" / "fb-001.md").exists()


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
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "adopt", bad], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err
