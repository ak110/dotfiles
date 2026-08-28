"""atk (agent-toolkit `atk mq`) のshowサブコマンドのテスト。

FILENAME指定表示（`--status`・`--answered`を迂回した全状態探索を含む）・--all全件表示・
型フィルター・状態フィルター・--skip-pullの単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_mq_list_test.py`・
`_atk_mq_mutations_test.py`・`_atk_mq_process_loop_test.py`に分離する。共通ヘルパーは`atk_test.py`から再利用する。
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
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar", body="本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-001.md"], home=tmp_path)

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
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "全状態フォルダに存在しません" in captured.err

    def test_target_repo_mismatch_falls_through_and_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定と--target-repo不一致時は次のkindへ進み、全kind該当なしでexit 2となる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "show", "fb-001.md", "--target-repo=github.com/example/bar"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "全状態フォルダに存在しません" in captured.err

    def test_filename_filter_matches_legacy_local_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定でも旧パス形の保存値を現行URL形と同一視する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_feedback_file(notes, "legacy.md", target_repo=str(local_repo), body="旧形式")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "show", "legacy.md", "--target-repo=github.com/example/myrepo", "--skip-pull"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert "旧形式" in capsys.readouterr().out


class TestShowMultipleFiles:
    """showサブコマンド: 複数FILENAMEを指定順にまとめて表示する。"""

    def test_multiple_files_preserve_order_and_pull_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数項目を指定順かつ空行区切りで表示し、pullを1回だけ実行する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        second = _write_feedback_file(notes, "fb-002.md", body="本文2")
        second.write_text(second.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-002.md", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.index("### fb-002.md") < captured.out.index("### fb-001.md")
        assert "本文2\n\n## target_repo" in captured.out
        fetches = [call for call in git_calls if call["cmd"][:2] == ["git", "fetch"]]
        assert len(fetches) == 1

    def test_multiple_files_filter_same_target_repo_without_extra_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一対象リポジトリの複数指定は1回の取得で対象を絞り、`--skip-pull`では同期しない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/foo", body="本文2")
        _write_feedback_file(notes, "other.md", target_repo="github.com/example/bar", body="対象外")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "show",
                    "fb-001.md",
                    "fb-002.md",
                    "--target-repo=github.com/example/foo",
                    "--skip-pull",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-001.md" in captured.out
        assert "### fb-002.md" in captured.out
        assert "対象外" not in captured.out
        assert not [call for call in git_calls if call["cmd"][:2] == ["git", "fetch"]]

    def test_multiple_files_use_same_separator_as_all(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数指定の区切りは`--all`と同じく各項目の後の空行1行とする。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        _write_feedback_file(notes, "fb-002.md", body="本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit):
            atk.main(["mq", "show", "fb-001.md", "fb-002.md"], home=tmp_path)
        by_name = capsys.readouterr().out

        with pytest.raises(SystemExit):
            atk.main(["mq", "show", "--all"], home=tmp_path)
        by_all = capsys.readouterr().out

        for entry_tail in ("本文1\n\n", "本文2\n\n"):
            assert entry_tail in by_name
            assert entry_tail in by_all
        assert by_name.endswith("\n\n")

    def test_single_file_keeps_output_without_extra_blank_line(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """1件だけ指定した場合は従来どおり末尾へ空行を足さない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit):
            atk.main(["mq", "show", "fb-001.md"], home=tmp_path)

        assert capsys.readouterr().out.endswith("本文1\n\n")

    def test_normalized_duplicates_are_shown_once_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """拡張子省略形と正規形の重複は初出1件へ集約し警告する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-001", "fb-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.count("### fb-001.md") == 1
        assert "showの引数リストに重複" in captured.err

    def test_any_missing_or_filter_mismatch_suppresses_all_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """欠落又はフィルター不一致が1件でもあれば全出力を抑制し該当名を全て列挙する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-ok.md", target_repo="github.com/example/foo", body="表示しない本文")
        _write_feedback_file(notes, "fb-mismatch.md", target_repo="github.com/example/bar", body="不一致本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "show",
                    "fb-ok.md",
                    "fb-mismatch.md",
                    "fb-missing.md",
                    "--target-repo=github.com/example/foo",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "fb-mismatch.md" in captured.err
        assert "fb-missing.md" in captured.err


class TestShowAll:
    """showサブコマンド: --all指定でtarget_repoごとにグループ化した全件本文を表示する。"""

    def test_all_filter_matches_legacy_local_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--allでも旧パス形と現行URL形を同じ対象集合として表示する。"""
        notes = _setup_notes(tmp_path)
        local_repo = tmp_path / "myrepo"
        local_repo.mkdir()
        _write_feedback_file(notes, "legacy.md", target_repo=str(local_repo), body="旧形式")
        _write_feedback_file(notes, "current.md", target_repo="github.com/example/myrepo", body="現行形式")
        _write_feedback_file(notes, "missing.md", target_repo=str(tmp_path / "missing"), body="対象外")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "show", "--all", "--target-repo=github.com/example/myrepo", "--skip-pull"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "legacy.md" in output
        assert "current.md" in output
        assert "missing.md" not in output

    def test_all_shows_every_entry_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all指定時は複数target_repoの全件がグループ化されて出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/bar", body="本文2")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", target_repo="github.com/example/foo", body="inbox本文")
        adopted_dir = notes / "adopted"
        adopted_dir.mkdir(parents=True, exist_ok=True)
        (adopted_dir / "fb-adopted.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\nadopted本文\n",
            encoding="utf-8",
        )
        rejected_dir = notes / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        (rejected_dir / "fb-rejected.md").write_text(
            "---\ntarget_repo: github.com/example/foo\ntype: feedback\n---\n\nrejected本文\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all", "--status=all"], home=tmp_path)

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
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "usage: atk mq show" in captured.err
        assert "FILENAME" in captured.err
        assert "--all" in captured.err
        error_line = captured.err.rstrip("\n").splitlines()[-1]
        assert "表示するファイル名または--allを指定してください" in error_line


class TestShowTypeFilter:
    """showサブコマンド: --typeでFILENAME探索対象種別を限定する（探索範囲は4状態フォルダ全体）。"""

    def test_type_tbd_finds_tbd_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=tbd指定時はinboxのみを探索しstatusラベル付きで出力する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", f"{_FIXED_TIMESTAMP}-001.md", "--type=tbd"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"### {_FIXED_TIMESTAMP}-001.md [inbox/unanswered]" in captured.out

    def test_type_feedback_excludes_tbd_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=feedback指定時はtbdエントリを種別不一致として除外し該当なしでexit 2になる。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", f"{_FIXED_TIMESTAMP}-001.md", "--type=feedback"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "全状態フォルダに存在しません" in captured.err

    def test_type_all_searches_feedback_then_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=all（既定）は種別を問わず探索し該当エントリを表示する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"### {_FIXED_TIMESTAMP}-001.md [inbox/unanswered]" in captured.out


class TestShowSourceFilter:
    """showサブコマンド: --source指定でfrontmatterのsourceが一致するエントリのみ出力する。"""

    def test_all_filter_matches_exact_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all --source=NAME指定時、同一sourceのエントリのみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1", source="session-review")
        _write_feedback_file(notes, "fb-002.md", target_repo="github.com/example/foo", body="本文2", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all", "--source=session-review"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "本文1" in captured.out
        assert "本文2" not in captured.out

    def test_filename_filter_negation_excludes_matching_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """FILENAME指定＋--source=!NAME指定時、一致するsourceのファイルは未検出扱いになる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", source="session-review")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-001.md", "--source=!session-review"], home=tmp_path)

        assert exc_info.value.code == 2

    def test_all_filter_matches_exact_source_for_tbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all --source=NAME指定時、tbd側も同一sourceのエントリのみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, "tbd-001.md", question="質問1", source="session-review")
        _write_tbd_file(notes, "tbd-002.md", question="質問2", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all", "--type=tbd", "--status=all", "--source=session-review"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "質問1" in captured.out
        assert "質問2" not in captured.out


class TestShowStatusFilter:
    """showサブコマンド: --answeredでTBDの回答状況を限定する（--all分岐のみ有効）。"""

    def test_all_answered_excludes_unanswered_tbd_and_feedback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all --answered=yesは回答済みTBDだけを表示する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-001.md" not in captured.out
        assert f"### {_FIXED_TIMESTAMP}-002.md [inbox/answered]" in captured.out
        assert f"{_FIXED_TIMESTAMP}-001.md" not in captured.out

    def test_filename_bypasses_answered_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定時は--answeredを迂回し、未回答tbdでも--answered=yesと無関係に表示される。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "show", f"{_FIXED_TIMESTAMP}-001.md", "--answered=yes"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"### {_FIXED_TIMESTAMP}-001.md [inbox/unanswered]" in captured.out


def _write_feedback_state_file(
    notes: pathlib.Path,
    state: str,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "state本文",
) -> pathlib.Path:
    """指定状態配下にフィードバックファイルを書き込み、絶対パスを返す。"""
    state_dir = notes / state
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / filename
    path.write_text(
        f"---\ntype: feedback\ntarget_repo: {target_repo}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _write_feedback_processing_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "processing本文",
) -> pathlib.Path:
    """processing配下に1ファイルを書き込み、絶対パスを返す（`_write_feedback_state_file`の薄いラッパー）。"""
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
        notes = _setup_notes(tmp_path)
        _write_feedback_processing_file(notes, "fb-processing.md", body="processing本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-processing.md"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-dup.md", body="inbox側本文")
        _write_feedback_processing_file(notes, "fb-dup.md", body="processing側本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-dup.md"], home=tmp_path)

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
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="inbox本文")
        _write_feedback_processing_file(notes, "fb-processing.md", body="processing本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-inbox.md" in captured.out
        assert "inbox本文" in captured.out
        assert "### fb-processing.md" in captured.out
        assert "processing本文" in captured.out


class TestShowProcessedStates:
    """showサブコマンド: 処理済み状態の探索と、FILENAME単発指定時の`--status`迂回を検証する。"""

    def test_finds_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """adopted配下のFILENAMEを参照できる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-adopted.md", "--status=adopted"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-adopted.md" in captured.out
        assert "adopted本文" in captured.out

    def test_finds_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rejected配下のFILENAMEを参照できる。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_state_file(notes, "rejected", "fb-rejected.md", body="rejected本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-rejected.md", "--status=rejected"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-rejected.md" in captured.out
        assert "rejected本文" in captured.out

    def test_default_status_still_finds_adopted_via_filename(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--status`未指定（既定active）でもFILENAME指定時はadopted配下を探索し表示できる。

        既定の`--status=active`をFILENAME単発指定分岐へ適用すると、`atk mq show <FILENAME>`
        でadopted・rejected状態のエントリを一切参照できなくなるため、単発指定は`--status`を
        迂回する契約になっている（指摘2の修正と対で成立する）。
        """
        notes = _setup_notes(tmp_path)
        _write_feedback_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-adopted.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-adopted.md" in captured.out
        assert "adopted本文" in captured.out

    def test_all_status_all_includes_processed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all --status=allは全状態を表示する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-inbox.md", body="inbox本文")
        _write_feedback_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        _write_feedback_state_file(notes, "rejected", "fb-rejected.md", body="rejected本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-inbox.md" in captured.out
        assert "adopted本文" in captured.out
        assert "rejected本文" in captured.out


class TestShowSkipPull:
    """showサブコマンド: --skip-pull指定時はremote同期全体をスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はfetch・merge・rebaseが実行されない。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "--all", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] in (["git", "fetch"], ["git", "merge"], ["git", "rebase"]) for c in git_calls)

    def test_recent_sync_is_reused_without_remote_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """直近の同期形跡がある通常表示ではremote同期を省略して再利用を案内する。"""
        notes = _setup_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-001.md"], home=tmp_path)

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
        _write_feedback_file(notes, "fb-001.md")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", "fb-001.md", "--pull"], home=tmp_path)

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
            atk.main(["mq", "show", "--all", "--skip-pull", "--pull"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "not allowed with argument" in capsys.readouterr().err


class TestShowStatePrefixedFilename:
    """showサブコマンド: 状態名付きファイル名は再実行方法を案内して終了する。"""

    @pytest.mark.parametrize("state", ["inbox", "processing", "planning", "adopted", "rejected"])
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

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "show", f"{state}/fb-001.md"], home=tmp_path)

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
            atk.main(["mq", "show", "unknown/fb-001.md"], home=tmp_path)

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
            atk.main(["mq", "show", "inbox/.."], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err
