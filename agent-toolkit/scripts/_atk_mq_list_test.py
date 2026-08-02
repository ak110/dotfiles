"""atk (agent-toolkit `atk mq`) のlistサブコマンドのテスト。

feedback/tbd一覧出力・各種フィルター（target-repo・source・type・status・skip-pull・count）の
単体テストを集約する。他サブコマンドの分割先はatk_test.pyの分割方針一覧docstringを参照する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import os
import pathlib
import shutil
import subprocess
import sys
import unicodedata

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_schedule as schedule  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position

# pylint: disable-next=wrong-import-position,import-error
from _atk_git_fake_test_helpers import make_git_remote_fake as _make_git_remote_fake  # noqa: E402
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
    _write_tbd_file,
)  # noqa: E402  # pylint: disable=wrong-import-position


class TestListEmpty:
    """listサブコマンド: inbox空の場合は何も出力しない。"""

    def test_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空時は標準出力が空であること。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListSingle:
    """listサブコマンド: 1件のフィードバックを1行で出力する。"""

    def test_single_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """1件のフィードバックがfilename・target_repo・本文冒頭要約のtab区切り1行で出力されること。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md: github.com/example/foo [inbox/unclassified/carry=0] 本文1\n"


class TestListPlanImplementationClassification:
    """独立キーを持つ計画実装型を未分類として分類委譲へ混入させない。"""

    def test_missing_schedule_metadata_is_labeled_plan_implementation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """queue_schedule欠落時もトップレベルplan_fileを優先して計画実装型と表示する。"""
        notes = _setup_notes(tmp_path)
        plan = tmp_path / "plan.md"
        path = _write_feedback_file(notes, "plan.md", target_repo="github.com/example/repo", body="本文")
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: feedback\n", f"type: feedback\nplan_file: {plan}\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "[inbox/plan-impl/carry=0]" in output
        assert "unclassified" not in output

    def test_stale_schedule_metadata_is_labeled_plan_implementation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """本文ハッシュ不一致時もトップレベルplan_fileを優先して計画実装型と表示する。"""
        notes = _setup_notes(tmp_path)
        plan = tmp_path / "plan.md"
        path = _write_feedback_file(notes, "plan.md", target_repo="github.com/example/repo", body="本文")
        text = path.read_text(encoding="utf-8").replace(
            "type: feedback\n",
            f"type: feedback\nplan_file: {plan}\n",
        )
        metadata = schedule.ScheduleMetadata(
            schedule.body_sha256(text),
            "github.com/example/repo",
            "plan-impl",
            schedule.Dependency("none"),
            str(plan),
            (),
            2,
            ("dependency-unmet", "conflict"),
        )
        path.write_text(schedule.serialize_schedule_metadata(text, metadata) + "\n本文変更\n", encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "[inbox/plan-impl/carry=0]" in output
        assert "unclassified" not in output


class TestListMalformedFrontmatter:
    """listサブコマンド: 異常frontmatterは`(unknown)`グループへ振り分けられる。"""

    @pytest.mark.parametrize(
        ("content", "label", "expected_exit"),
        [
            ("本文のみ\n", "frontmatterなし", 0),
            ("---\ncreated: 2024\n本文\n", "閉じ区切りなし", 0),
            ("---\ncreated: 2024\n---\n\n本文\n", "target_repo欠落", 2),
        ],
    )
    def test_malformed_frontmatter_falls_back_to_unknown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        content: str,
        label: str,
        expected_exit: int,
    ) -> None:
        """typeを確定できない異常frontmatter形式は拒否される。"""
        del label  # parametrize idのみ
        notes = _setup_notes(tmp_path)
        (notes / "inbox" / "malformed.md").write_text(content, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == expected_exit
        captured = capsys.readouterr()
        if expected_exit == 0:
            assert "[inbox/frontmatter-broken/carry=0]" in captured.out
            assert not captured.err
        else:
            assert not captured.out
            assert "frontmatterのtypeが不正または欠落" in captured.err


class TestListMultipleRepos:
    """listサブコマンド: 複数target_repo混在でも1件1行で全件出力される。"""

    def test_multiple_repos_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target_repoが異なる複数のフィードバックがそれぞれ1行で出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "# feedback",
            "fb-001.md: github.com/example/foo [inbox/unclassified/carry=0] テスト本文",
            "fb-002.md: github.com/example/bar [inbox/unclassified/carry=0] テスト本文",
        ]

    def test_type_groups_sort_by_filename_independently_of_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """種別見出しを維持し、各グループ内を状態によらずファイル名順に並べる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "z-feedback.md", body="フィードバック")
        feedback_processing = _write_feedback_file(notes, "a-feedback.md", body="処理中フィードバック")
        (notes / "processing").mkdir()
        feedback_processing.replace(notes / "processing" / feedback_processing.name)
        _write_tbd_file(notes, "z-tbd.md", question="未回答", answer="")
        tbd_processing = _write_tbd_file(notes, "a-tbd.md", question="回答済み", answer="回答")
        tbd_processing.replace(notes / "processing" / tbd_processing.name)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        lines = output.splitlines()
        assert [line.split(":", 1)[0] for line in lines] == [
            "# feedback",
            "a-feedback.md",
            "z-feedback.md",
            "# tbd",
            "a-tbd.md",
            "z-tbd.md",
        ]
        assert "[processing/" in lines[1]
        assert "[inbox/" in lines[2]
        assert "[processing/answered/" in lines[4]
        assert "[inbox/unanswered]" in lines[5]


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
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--target-repo=github.com/example/foo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "github.com/example/foo" in captured.out
        assert "github.com/example/bar" not in captured.out

    def test_filter_expands_tilde(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """~プレフィックスのローカルパスがgit remote get-urlで正規化され、対応するエントリが出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/myrepo")
        monkeypatch.setenv("HOME", str(tmp_path))

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--target-repo=~/myrepo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md: github.com/example/myrepo [inbox/unclassified/carry=0] テスト本文\n"

    def test_filter_no_match_outputs_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """一致するエントリが存在しない場合、標準出力は空になる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--target-repo=github.com/example/nomatch"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListSourceFilter:
    """listサブコマンド: --source指定でfrontmatterのsourceが一致するエントリのみ出力する。"""

    @pytest.mark.parametrize(
        ("source_filter", "expected_filename", "excluded_filename"),
        [
            ("session-review", "fb-001.md", "fb-002.md"),
            ("!session-review", "fb-002.md", "fb-001.md"),
        ],
    )
    def test_source_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        source_filter: str,
        expected_filename: str,
        excluded_filename: str,
    ) -> None:
        """--sourceの一致指定と否定指定が該当エントリだけを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", source="session-review")
        _write_feedback_file(notes, "fb-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", f"--source={source_filter}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert expected_filename in captured.out
        assert excluded_filename not in captured.out

    @pytest.mark.parametrize("value", ["--source=", "--source=!"])
    def test_empty_source_value_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        value: str,
    ) -> None:
        """--source=・--source=!（空文字列）はargparseエラーでexit 2する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", value], home=tmp_path)

        assert exc_info.value.code == 2

    def test_filter_matches_exact_source_for_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--source=NAME指定時、tbd側も同一sourceのエントリのみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, "tbd-001.md", source="session-review")
        _write_tbd_file(notes, "tbd-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=tbd", "--status=all", "--source=session-review"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "tbd-001.md" in captured.out
        assert "tbd-002.md" not in captured.out


class TestListTypeFilter:
    """listサブコマンド: --typeでfeedback/tbd出力を限定する。"""

    def test_type_feedback_outputs_only_feedback_section(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=feedback指定時はfeedback部のみ出力されtbdヘッダは出力されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=feedback"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md: github.com/example/foo [inbox/unclassified/carry=0] 本文1\n"

    def test_type_tbd_outputs_status_label(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=tbd指定時はtbd部のみ出力され回答状況ラベルが付与される。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=tbd", "--answered=no"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# tbd\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"

    def test_answered_tbd_status_label_omits_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """回答済みTBDの状態ラベルは種別を含まない従来形式である。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="回答")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=tbd", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/answered/" in captured.out
        assert "[tbd/" not in captured.out

    def test_type_all_omits_empty_section_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=all（既定）でtbd側が0件の場合はtbd種別ヘッダを省略する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# feedback" in captured.out
        assert "# tbd" not in captured.out


class TestListSkipPull:
    """listサブコマンド: --skip-pull指定時はgit pullをスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はgit pull --ff-onlyが実行されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] == ["git", "pull"] for c in git_calls)


class TestListStatusFilter:
    """listサブコマンド: --answeredでtbd側のみ回答状況を限定する。"""

    @pytest.mark.parametrize(
        ("answered", "expected_suffix", "excluded_suffix"),
        [
            ("yes", "002.md", "001.md"),
            ("no", "001.md", "002.md"),
        ],
    )
    def test_answered_status_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        answered: str,
        expected_suffix: str,
        excluded_suffix: str,
    ) -> None:
        """--answered=yes/noが回答状況と一致するTBDだけを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=tbd", f"--answered={answered}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-{expected_suffix}" in captured.out
        assert f"{_FIXED_TIMESTAMP}-{excluded_suffix}" not in captured.out

    def test_status_all_outputs_every_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=all指定時に全TBDが出力される。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=tbd", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md" in captured.out
        assert f"{_FIXED_TIMESTAMP}-002.md" in captured.out

    def test_status_answered_does_not_affect_feedback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--answered=yes指定時に回答概念を持たないfeedbackは除外される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# feedback" not in captured.out
        assert f"{_FIXED_TIMESTAMP}-001.md" not in captured.out

    def test_status_invalid_choice_exits_2(self, tmp_path: pathlib.Path) -> None:
        """--statusに不正値を指定するとargparseがexit 2で終了する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--status=invalid"], home=tmp_path)

        assert exc_info.value.code == 2

    @pytest.mark.parametrize("status", ["active", "rejected"])
    def test_active_and_rejected_are_accepted_choices(self, status: str) -> None:
        """--status=active・--status=rejectedがargparseのchoicesとして受理されること。

        機能的な出力検証（feedback側の状態別除外・tbd側の回答状況連動）は
        `_atk_mq_extras_test.py`のTestListFeedbackStatusActive・
        TestListFeedbackStatusRejectedへ集約する。
        """
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["mq", "list", f"--status={status}"])
        assert args.status == status


class TestListCount:
    """listサブコマンド: --count指定時は種別ヘッダ・エントリ行を抑制し件数のみ出力する。"""

    def test_count_outputs_total_of_feedback_and_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--count指定時にfeedback件数とTBD件数の合計が整数1行で出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--count", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "3\n"

    def test_count_suppresses_headers_and_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--count指定時は種別ヘッダ・エントリ行を出力しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--count"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "1\n"

    def test_count_with_status_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--countと--statusを併用すると、statusフィルター適用後の件数が出力される。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=tbd", "--answered=yes", "--count"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "1\n"

    def test_count_empty_inbox_outputs_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空時は0を出力する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--count"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "0\n"


class TestMultipleFiltersCombinedAsAnd:
    """target-repo・source・type・status・answeredの同時指定がAND条件で対象を限定する。

    `--answered`はfeedbackを無条件除外する仕様（`_answered_matches`が`entry_type != MQ_TYPE_TBD`時に
    `False`を返す）のため、`--answered=no`とtype不一致（feedback）を1回の呼び出しへ同居させると
    type条件の除外効果がanswered条件の除外効果と区別できなくなる。
    target-repo・source・type・statusの4条件は`--answered=all`（無効化）の下で検証し、
    answered条件は同一の4条件（tbdのみ）を満たすエントリ同士の回答有無差分で別途検証する。
    """

    def test_target_repo_source_type_status_combined_narrows_to_intersection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target-repo・source・type・statusの4条件全てに一致するtbdだけを出力する（answeredは無効化）。"""
        matching_repo = "github.com/example/matching"
        notes = _setup_notes(tmp_path)
        # 4条件全てに一致する唯一のエントリ（tbd・inbox）。
        _write_tbd_file(notes, "tbd-matching.md", target_repo=matching_repo, source="session-review")
        # target-repoのみ不一致。
        _write_tbd_file(
            notes,
            "tbd-other-repo.md",
            target_repo="github.com/example/other",
            source="session-review",
        )
        # sourceのみ不一致。
        _write_tbd_file(notes, "tbd-other-source.md", target_repo=matching_repo, source="user-issue")
        # typeのみ不一致（--answered=allのためfeedbackも回答状況フィルターでは除外されない）。
        _write_feedback_file(notes, "fb-other-type.md", target_repo=matching_repo, source="session-review")
        # statusのみ不一致（processing配下、--status=inboxで除外される）。
        processing_dir = notes / "processing"
        processing_dir.mkdir(parents=True, exist_ok=True)
        (processing_dir / "tbd-other-status.md").write_text(
            f"---\ntarget_repo: {matching_repo}\ntype: tbd\nquestion_type: free-form\n"
            "source: session-review\n---\n\n## 質問\n\n本文\n\n## 回答\n\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "list",
                    f"--target-repo={matching_repo}",
                    "--source=session-review",
                    "--type=tbd",
                    "--status=inbox",
                    "--answered=all",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "tbd-matching.md" in captured.out
        assert "tbd-other-repo.md" not in captured.out
        assert "tbd-other-source.md" not in captured.out
        assert "fb-other-type.md" not in captured.out
        assert "tbd-other-status.md" not in captured.out

    def test_answered_narrows_within_already_matching_four_conditions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target-repo・source・type・statusが一致する2エントリのうち、未回答のみが`--answered=no`で残る。"""
        matching_repo = "github.com/example/matching"
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, "tbd-unanswered.md", target_repo=matching_repo, source="session-review", answer="")
        _write_tbd_file(
            notes,
            "tbd-answered.md",
            target_repo=matching_repo,
            source="session-review",
            answer="回答済み",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "list",
                    f"--target-repo={matching_repo}",
                    "--source=session-review",
                    "--type=tbd",
                    "--status=inbox",
                    "--answered=no",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "tbd-unanswered.md" in captured.out
        assert "tbd-answered.md" not in captured.out


class TestListNarrowTerminalTargetRepo:
    """listサブコマンド: 狭幅端末の出力行が端末表示幅以内に収まることを検証する。"""

    _LONG_REPO = "github.com/organization-name/very-long-repository-name-example"

    @staticmethod
    def _display_width(text: str) -> int:
        """東アジア文字幅に基づく出力文字列の表示幅を返す。"""
        return sum(2 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 1 for char in text)

    def test_feedback_narrow_terminal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """feedback部の各出力行が50桁以内に収まる。"""
        terminal_columns = 70

        def get_terminal_size(*args: object, **kwargs: object) -> os.terminal_size:
            del args, kwargs
            return os.terminal_size((terminal_columns, 24))

        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo=self._LONG_REPO, body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(shutil, "get_terminal_size", get_terminal_size)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()
        assert self._LONG_REPO not in captured.out
        assert all(self._display_width(line) <= terminal_columns for line in output_lines)

    def test_tbd_narrow_terminal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """tbd部の各出力行が70桁以内に収まる。"""
        terminal_columns = 70

        def get_terminal_size(*args: object, **kwargs: object) -> os.terminal_size:
            del args, kwargs
            return os.terminal_size((terminal_columns, 24))

        notes = _setup_notes(tmp_path)
        _write_tbd_file(
            notes,
            f"{_FIXED_TIMESTAMP}-001.md",
            question="q1",
            answer="",
            target_repo=self._LONG_REPO,
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        monkeypatch.setattr(shutil, "get_terminal_size", get_terminal_size)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "list", "--type=tbd", "--answered=no"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()
        assert self._LONG_REPO not in captured.out
        assert all(self._display_width(line) <= terminal_columns for line in output_lines)
