"""atk (agent-toolkit `atk fb`) のlistサブコマンドのテスト。

feedback/tbd一覧出力・各種フィルター（target-repo・source・type・status・skip-pull・count）の
単体テストを集約する。他サブコマンドの分割先はatk_test.pyの分割方針一覧docstringを参照する。
共通ヘルパーは`atk_test.py`から再利用する。
"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position

# pylint: disable-next=wrong-import-position,import-error
from _atk_git_fake_test_helpers import make_git_remote_fake as _make_git_remote_fake  # noqa: E402
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_flag_and_notes,
    _setup_tbd_env,
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
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list"], home=tmp_path)

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
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md: github.com/example/foo [inbox] 本文1\n"


class TestListMalformedFrontmatter:
    """listサブコマンド: 異常frontmatterは`(unknown)`グループへ振り分けられる。"""

    @pytest.mark.parametrize(
        ("content", "label"),
        [
            ("本文のみ\n", "frontmatterなし"),
            ("---\ncreated: 2024\n本文\n", "閉じ区切りなし"),
            ("---\ncreated: 2024\n---\n\n本文\n", "target_repo欠落"),
        ],
    )
    def test_malformed_frontmatter_falls_back_to_unknown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        content: str,
        label: str,
    ) -> None:
        """異常frontmatter形式は`(unknown)`のtarget_repo欄として出力される。"""
        del label  # parametrize idのみ
        notes = _setup_flag_and_notes(tmp_path)
        (notes / "feedback" / "inbox" / "malformed.md").write_text(content, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("# feedback\nmalformed.md: (unknown) [inbox] ")


class TestListMultipleRepos:
    """listサブコマンド: 複数target_repo混在でも1件1行で全件出力される。"""

    def test_multiple_repos_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target_repoが異なる複数のフィードバックがそれぞれ1行で出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "# feedback",
            "fb-001.md: github.com/example/foo [inbox] テスト本文",
            "fb-002.md: github.com/example/bar [inbox] テスト本文",
        ]


class TestListTargetRepoFilter:
    """listサブコマンド: --target-repo指定で一致するエントリのみ出力する。"""

    def test_filter_matches_single_group(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数target_repo混在でも--target-repo指定値と一致するエントリのみ出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--target-repo=github.com/example/foo"], home=tmp_path)

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
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/myrepo")
        monkeypatch.setenv("HOME", str(tmp_path))

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--target-repo=~/myrepo"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md: github.com/example/myrepo [inbox] テスト本文\n"

    def test_filter_no_match_outputs_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """一致するエントリが存在しない場合、標準出力は空になる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--target-repo=github.com/example/nomatch"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListSourceFilter:
    """listサブコマンド: --source指定でfrontmatterのsourceが一致するエントリのみ出力する。"""

    def test_filter_matches_exact_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--source=NAME指定時、同一sourceのエントリのみ出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", source="session-review")
        _write_feedback_file(notes, "fb-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--source=session-review"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-001.md" in captured.out
        assert "fb-002.md" not in captured.out

    def test_filter_negation_excludes_matching_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--source=!NAME指定時、同一sourceのエントリが除外され、無指定エントリは含まれる。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", source="session-review")
        _write_feedback_file(notes, "fb-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--source=!session-review"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-001.md" not in captured.out
        assert "fb-002.md" in captured.out

    @pytest.mark.parametrize("value", ["--source=", "--source=!"])
    def test_empty_source_value_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        value: str,
    ) -> None:
        """--source=・--source=!（空文字列）はargparseエラーでexit 2する。"""
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", value], home=tmp_path)

        assert exc_info.value.code == 2

    def test_filter_matches_exact_source_for_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--source=NAME指定時、tbd側も同一sourceのエントリのみ出力される。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, "tbd-001.md", source="session-review")
        _write_tbd_file(notes, "tbd-002.md", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=tbd", "--status=all", "--source=session-review"], home=tmp_path)

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
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=feedback"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# feedback\nfb-001.md: github.com/example/foo [inbox] 本文1\n"

    def test_type_tbd_outputs_status_label(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=tbd指定時はtbd部のみ出力され回答状況ラベルが付与される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=tbd", "--status=unanswered"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# tbd\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [unanswered] q1\n"

    def test_type_all_omits_empty_section_header(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=all（既定）でtbd側が0件の場合はtbd種別ヘッダを省略する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list"], home=tmp_path)

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
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] == ["git", "pull"] for c in git_calls)


class TestListStatusFilter:
    """listサブコマンド: --statusでtbd側のみ回答状況を限定する。"""

    def test_status_answered_excludes_unanswered_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=answered指定時に未回答TBDが除外される。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=tbd", "--status=answered"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md" not in captured.out
        assert f"{_FIXED_TIMESTAMP}-002.md" in captured.out

    def test_status_unanswered_excludes_answered_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=unanswered指定時に回答済みTBDが除外される。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=tbd", "--status=unanswered"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md" in captured.out
        assert f"{_FIXED_TIMESTAMP}-002.md" not in captured.out

    def test_status_all_outputs_every_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=all指定時に全TBDが出力される。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=tbd", "--status=all"], home=tmp_path)

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
        """--status=answered指定時にfeedback側は影響を受けず全件出力される。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--status=answered"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# feedback\nfb-001.md: github.com/example/foo [inbox] 本文1\n" in captured.out
        assert f"{_FIXED_TIMESTAMP}-001.md" not in captured.out

    def test_status_invalid_choice_exits_2(self, tmp_path: pathlib.Path) -> None:
        """--statusに不正値を指定するとargparseがexit 2で終了する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--status=invalid"], home=tmp_path)

        assert exc_info.value.code == 2

    @pytest.mark.parametrize("status", ["active", "rejected"])
    def test_active_and_rejected_are_accepted_choices(self, status: str) -> None:
        """--status=active・--status=rejectedがargparseのchoicesとして受理されること。

        機能的な出力検証（feedback側の状態別除外・tbd側の回答状況連動）は
        `_atk_fb_extras_test.py`のTestListFeedbackStatusActive・
        TestListFeedbackStatusRejectedへ集約する。
        """
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["fb", "list", f"--status={status}"])
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
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        _write_feedback_file(notes, "fb-002.md")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--count", "--status=all"], home=tmp_path)

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
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--count"], home=tmp_path)

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
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--type=tbd", "--status=answered", "--count"], home=tmp_path)

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
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "list", "--count"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "0\n"
