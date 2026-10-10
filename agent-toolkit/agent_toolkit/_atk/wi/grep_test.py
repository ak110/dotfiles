"""atk (agent-toolkit `atk wi`) のgrepサブコマンドのテスト。

本文全体（frontmatter含む）の正規表現検索・大文字小文字無視・各種フィルター
（target-repo・type・status・answered・source）・該当0件時のexit 1・不正な正規表現時のexit 2の
単体テストに加え、件数・ファイル名・要約の出力形式と固定文字列の検索が行の列挙と同じ項目選択へ
作用することを確かめるテストを集約する。共通ヘルパーは`atk_test.py`から再利用する。
"""

import collections
import json
import pathlib
import re
import subprocess

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._testing.git_fakes import (  # noqa: E402  # pylint: disable=wrong-import-position
    make_git_remote_fake as _make_git_remote_fake,
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit.atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
    _write_uwi_file,
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
        _write_awi_file(notes, "fb-001.md", body="line1\nline2\nline3")
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
        _write_awi_file(notes, "fb-001.md", body="line1")
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
        """直近の同期形跡がある通常検索ではremote同期を省略し、注記を付けない。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="searchword")
        git_dir = notes / ".git"
        git_dir.mkdir()
        (git_dir / "FETCH_HEAD").touch()
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword"], home=tmp_path)

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
        _write_awi_file(notes, "fb-001.md", body="searchword")
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
        _write_awi_file(notes, "fb-001.md", body="Uppercase")
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
        _write_awi_file(notes, "fb-001.md", body="Uppercase")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "uppercase", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""


class TestGrepFilters:
    """grepサブコマンド: 各フィルターが`list`と同じ意味で作用すること。"""

    def test_adopted_session_review_search_limits_entries_before_matching(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """終端済み振り返りの検索対象は状態・投入元・対象リポジトリの積集合となる。"""
        notes = _setup_notes(tmp_path)
        adopted_dir = notes / "adopted"
        adopted_dir.mkdir()
        for filename, source, repo in (
            ("matched.md", "session-review", "github.com/example/foo"),
            ("other-source.md", "manual", "github.com/example/foo"),
            ("other-repo.md", "session-review", "github.com/example/other"),
        ):
            path = _write_awi_file(notes, filename, target_repo=repo, body="shared phrase", source=source)
            path.rename(adopted_dir / filename)
        _write_awi_file(notes, "active.md", body="shared phrase", source="session-review")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "grep",
                    "shared phrase",
                    "--state=adopted",
                    "--source=session-review",
                    "--target-repo=github.com/example/foo",
                    "--skip-pull",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert capsys.readouterr().out.strip() == "matched.md:7:shared phrase"

    def test_type_filter_limits_to_awi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--type=awi`でAWI種別のみを対象とする。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="searchword")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="searchword")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--type=awi", "--skip-pull"], home=tmp_path)

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
        _write_awi_file(notes, "legacy.md", target_repo=str(local_repo), body="searchword")
        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(local_repo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "grep", "searchword", "--target-repo=github.com/example/myrepo", "--skip-pull"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert "legacy.md" in capsys.readouterr().out

    def test_type_filter_limits_to_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=uwiでuwi種別のみを対象とする。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="searchword")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="searchword")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--type=uwi", "--skip-pull"], home=tmp_path)

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
        """状態を指定せずactiveを使うgrepはhold配下のawiとUWIをいずれも含める。"""
        notes = _setup_notes(tmp_path)
        hold_dir = notes / "hold"
        hold_dir.mkdir(parents=True, exist_ok=True)
        (hold_dir / "hold-awi.md").write_text(
            "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\nsearchword\n",
            encoding="utf-8",
        )
        (hold_dir / "hold-uwi.md").write_text(
            "---\ntype: uwi\ntarget_repo: github.com/example/foo\n---\n\nsearchword\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "hold-awi.md" in captured.out
        assert "hold-uwi.md" in captured.out

    def test_answered_filter_limits_to_unanswered(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--answered=noで未回答UWIのみを対象とする。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="searchword", answer="")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="other", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "searchword", "--type=uwi", "--answered=no", "--skip-pull"], home=tmp_path)

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
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/searchword", body="body")
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
        _write_awi_file(notes, "fb-001.md", body="text")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", "[invalid(regex", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "正規表現が不正です" in captured.err
        # 構文の確かめ方とエスケープを次の操作として続ける。
        assert "エスケープして再実行する" in captured.err.split("\n次の操作: ", 1)[1]


def _run_wi(
    argv: list[str],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int | str | None, str, str]:
    """`atk wi <argv>`を実行し、終了コード・標準出力・標準エラーを返す。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", *argv, "--skip-pull"], home=tmp_path)
    captured = capsys.readouterr()
    return exc_info.value.code, captured.out, captured.err


def _write_output_form_queue(tmp_path: pathlib.Path) -> None:
    """出力形式の検証用に、状態・種別・投入元・対象リポジトリ・回答状況の異なる項目を置く。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "multi.md", body="# 複数行の項目\n\nneedle first\nunrelated\nNeedle second\nneedle third")
    _write_awi_file(notes, "single.md", body="# 1行の項目\n\nneedle only", source="session-review")
    _write_awi_file(notes, "none.md", body="# 一致しない項目\n\nbar")
    _write_awi_file(notes, "other-repo.md", target_repo="github.com/example/other", body="# 別リポジトリ\n\nneedle")
    _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="needle unanswered")
    _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="needle answered", answer="回答あり\n")
    adopted_dir = notes / "adopted"
    adopted_dir.mkdir()
    _write_awi_file(notes, "adopted.md", body="# 終端済み\n\nneedle").rename(adopted_dir / "adopted.md")


class TestGrepOutputForms:
    """grepサブコマンド: 件数・ファイル名・要約の出力形式が行の列挙と同じ項目と一致行を扱う。

    保証する契約は、各形式が行の列挙と同じ選択条件と行単位の一致判定で項目を選ぶことである。
    形式ごとの処理が選択条件か一致判定を別に実装すると、行の列挙から数えた値と一致しなくなる。
    """

    @pytest.mark.parametrize(
        "condition",
        [
            ["needle", "--target-repo=all"],
            ["needle", "--target-repo=all", "--state=all"],
            ["needle", "--target-repo=all", "--type=awi"],
            ["needle", "--target-repo=all", "--type=uwi", "--answered=no"],
            ["needle", "--target-repo=all", "--source=session-review"],
            ["needle", "--target-repo=github.com/example/foo"],
            ["needle", "--target-repo=all", "--ignore-case"],
        ],
    )
    def test_grep_output_forms_match_default_selection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        condition: list[str],
    ) -> None:
        """3形式の出力が行の列挙の項目ごとの行数と一致し、一致行の本文を含まない。"""
        _write_output_form_queue(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        code, default_out, _ = _run_wi(["grep", *condition], tmp_path, capsys)
        assert code == 0
        default_lines = default_out.splitlines()
        assert all(re.fullmatch(r"[^:]+\.md:\d+:.*", line) for line in default_lines)
        expected_counts = collections.Counter(line.split(":", 1)[0] for line in default_lines)
        expected_order = list(expected_counts)

        for option in ("-c", "--count"):
            code, out, _ = _run_wi(["grep", option, *condition], tmp_path, capsys)
            assert code == 0
            assert out.splitlines() == [f"{name}:{count}" for name, count in expected_counts.items()]

        for option in ("-l", "--files-with-matches"):
            code, out, _ = _run_wi(["grep", option, *condition], tmp_path, capsys)
            assert code == 0
            assert out.splitlines() == expected_order

        code, out, _ = _run_wi(["grep", "--summary-only", *condition], tmp_path, capsys)
        assert code == 0
        records = [json.loads(line) for line in out.splitlines()]
        assert [record["filename"] for record in records] == expected_order
        assert {record["filename"]: record["match_count"] for record in records} == dict(expected_counts)
        # 一致行の本文は返さない（UWIの要約は質問本文の先頭行のため、AWIの本文行で確かめる）。
        assert "needle first" not in out

    def test_grep_summary_matches_list_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--summary-only`の`filename`と`summary`は`atk wi list --summary-only`の同じ項目と同じ値になる。"""
        _write_output_form_queue(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        code, list_out, _ = _run_wi(["list", "--summary-only", "--state=all", "--target-repo=all"], tmp_path, capsys)
        assert code in (0, None)
        list_summaries = {record["filename"]: record["summary"] for record in map(json.loads, list_out.splitlines())}
        code, out, _ = _run_wi(["grep", "--summary-only", "needle", "--state=all", "--target-repo=all"], tmp_path, capsys)
        assert code == 0
        records = [json.loads(line) for line in out.splitlines()]
        assert len(records) == 6
        for record in records:
            assert set(record) == {"filename", "summary", "match_count"}
            assert record["summary"] == list_summaries[record["filename"]]

    @pytest.mark.parametrize("option", ["-c", "--count", "-l", "--files-with-matches", "--summary-only"])
    def test_grep_output_form_no_match_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        option: str,
    ) -> None:
        """一致0件は行の列挙と同じく標準エラーの`該当0件: `と終了コード1になる。"""
        _write_output_form_queue(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        code, out, err = _run_wi(["grep", option, "nonexistent", "--target-repo=all"], tmp_path, capsys)

        assert code == 1
        assert out == ""
        assert any(line.startswith("該当0件: ") for line in err.splitlines())

    @pytest.mark.parametrize(
        "options",
        [["-c", "-l"], ["--count", "--summary-only"], ["--files-with-matches", "--summary-only"]],
    )
    def test_grep_output_forms_are_mutually_exclusive(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        options: list[str],
    ) -> None:
        """2つの出力形式の併用は使い方の誤りとして終了コード2になる。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "grep", *options, "needle", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "not allowed with argument" in capsys.readouterr().err


class TestGrepFixedStrings:
    """grepサブコマンド: `-F`・`--fixed-strings`でPATTERNを文字そのものとして扱う。

    保証する契約は、固定文字列の指定がパターンの解釈だけを変え、選択条件と出力は`-F`を指定しない場合と同じ処理を使うことである。
    """

    @staticmethod
    def _write_queue(tmp_path: pathlib.Path) -> None:
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "bracket.md", body="text[1] 文字\ntext1 文字")
        _write_awi_file(notes, "pipe.md", body="a|b\na only\nb only\nA|B")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="a|b in uwi")

    @pytest.mark.parametrize("option", ["-F", "--fixed-strings"])
    def test_grep_fixed_strings_matches_escaped_regex(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        option: str,
    ) -> None:
        """記号を含む語の固定文字列検索は、エスケープした正規表現と同じ行を返す。"""
        self._write_queue(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        code, fixed_out, _ = _run_wi(["grep", option, "text[1] 文字"], tmp_path, capsys)
        assert code == 0
        escaped_code, escaped_out, _ = _run_wi(["grep", r"text\[1\] 文字"], tmp_path, capsys)
        assert escaped_code == 0
        assert fixed_out == escaped_out
        assert fixed_out.splitlines() == ["bracket.md:6:text[1] 文字"]

    def test_grep_fixed_strings_matches_literal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`-F`の`a|b`は文字列そのものに一致し、`-F`なしでは正規表現の選択として一致する。"""
        self._write_queue(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        code, out, _ = _run_wi(["grep", "-F", "a|b", "--type=awi"], tmp_path, capsys)
        assert code == 0
        assert out.splitlines() == ["pipe.md:6:a|b"]

        code, out, _ = _run_wi(["grep", "a|b", "--type=awi"], tmp_path, capsys)
        assert code == 0
        assert "pipe.md:7:a only" in out.splitlines()
        assert "pipe.md:8:b only" in out.splitlines()

    def test_grep_fixed_strings_with_ignore_case_and_filters(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`-F`と`-i`の併用は大文字小文字を無視し、`--type`は`-F`なしと同じ項目へ作用する。"""
        self._write_queue(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        code, out, _ = _run_wi(["grep", "-F", "-i", "a|b", "--type=awi"], tmp_path, capsys)
        assert code == 0
        assert out.splitlines() == ["pipe.md:6:a|b", "pipe.md:9:A|B"]

        code, out, _ = _run_wi(["grep", "-F", "-l", "a|b", "--type=uwi"], tmp_path, capsys)
        assert code == 0
        assert out.splitlines() == [f"{_FIXED_TIMESTAMP}-001.md"]

    def test_grep_fixed_strings_no_match_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`-F`で一致しない語は`該当0件: `と終了コード1になる。正規表現として不正な語もエラーにならない。"""
        self._write_queue(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        code, out, err = _run_wi(["grep", "-F", "[invalid(regex"], tmp_path, capsys)

        assert code == 1
        assert out == ""
        assert any(line.startswith("該当0件: ") for line in err.splitlines())
