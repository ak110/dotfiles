# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのlisting: outputのテスト。"""

import json
import os
import pathlib
import shutil
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.wi_listing_support import _AGENT_ENVIRONMENT_VARIABLES
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _FIXED_TIMESTAMP, _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


class TestListCount:
    """listサブコマンド: --count指定時は種別ヘッダ・エントリ行を抑制し件数のみ出力する。"""

    def test_count_outputs_total_of_awi_and_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--count`指定時にAWI件数とUWI件数の合計が整数1行で出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md")
        _write_awi_file(notes, "fb-002.md")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--count", "--status=all"], home=tmp_path)

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
        _write_awi_file(notes, "fb-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--count"], home=tmp_path)

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
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered=yes", "--count"], home=tmp_path)

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
            atk.main(["wi", "list", "--count"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "0\n"


class TestListJson:
    """listサブコマンド: --jsonは端末幅によらず同じ形式のJSON Linesを返す。"""

    def test_json_preserves_long_fields_and_reports_readiness(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """狭い端末でも本文要約・target_repo・blocked理由を切り詰めず出力する。"""
        notes = _setup_notes(tmp_path)
        long_repo = "github.com/example/" + "repository-" * 12
        body = "本文の長い要約 " + "長文" * 100
        path = _write_awi_file(notes, "json.md", target_repo=long_repo, body=body)
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [missing.md]\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        def get_terminal_size(*args: object, **kwargs: object) -> os.terminal_size:
            del args, kwargs
            return os.terminal_size((40, 24))

        monkeypatch.setattr(shutil, "get_terminal_size", get_terminal_size)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--jsonl"], home=tmp_path)

        assert exc_info.value.code == 0
        record = json.loads(capsys.readouterr().out)
        assert record["filename"] == "json.md"
        assert record["type"] == "awi"
        assert record["target_repo"] == long_repo
        assert record["summary"] == body
        assert record["ready"] is False
        assert record["blocked_reason"] == "missing-dependency"

    def test_json_splits_dependency_unmet_by_candidate_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """依存の未充足を、未終了の依存先が同じ出力へ現れるかで2値へ分けて報告する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "leader.md")
        _write_awi_file(notes, "external.md", target_repo="github.com/example/bar")
        inner = _write_awi_file(notes, "inner.md")
        outer = _write_awi_file(notes, "outer.md")
        inner.write_text(
            inner.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [leader.md]\n"),
            encoding="utf-8",
        )
        outer.write_text(
            outer.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ndepends_on: [external.md]\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "list", "--jsonl", "--skip-pull", "--target-repo=github.com/example/foo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        reasons = {record["filename"]: record["blocked_reason"] for record in records}
        assert reasons == {
            "inner.md": "dependency-unmet-internal",
            "leader.md": None,
            "outer.md": "dependency-unmet-external",
        }

    def test_json_and_count_are_mutually_exclusive(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--jsonと--countの同時指定はargparseの利用エラーとなる。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--jsonl", "--count"], home=tmp_path)

        assert exc_info.value.code == 2
        assert "not allowed with argument" in capsys.readouterr().err

    @pytest.mark.parametrize("environment_name", _AGENT_ENVIRONMENT_VARIABLES)
    def test_agent_environment_defaults_to_json_lines(
        self,
        environment_name: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """各エージェント環境変数で明示指定なしの出力をJSON Linesにする。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "awi.md", body="全文")
        monkeypatch.setenv(environment_name, "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        assert json.loads(capsys.readouterr().out)["filename"] == "awi.md"

    def test_no_json_overrides_agent_default_and_count_keeps_integer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エージェント環境でも明示テキストと件数形式を優先する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "awi.md", body="全文")
        monkeypatch.setenv("CODEX_CI", "1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as text_exit:
            atk.main(["wi", "list", "--no-jsonl"], home=tmp_path)
        assert text_exit.value.code == 0
        assert capsys.readouterr().out.startswith("# awi\n")

        with pytest.raises(SystemExit) as count_exit:
            atk.main(["wi", "list", "--count"], home=tmp_path)
        assert count_exit.value.code == 0
        assert capsys.readouterr().out == "1\n"


def test_list_summary_only_outputs_filename_and_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """要約出力はAWIとUWIをファイル名順の最小JSONへ変換する。"""
    notes = _setup_notes(tmp_path)
    _write_awi_file(notes, "z-awi.md", body="AWIの要約")
    _write_uwi_file(notes, "a-uwi.md", question="UWIの質問", answer="")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "list", "--summary-only", "--skip-pull"], home=tmp_path)

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == [
        {"filename": "a-uwi.md", "summary": "UWIの質問"},
        {"filename": "z-awi.md", "summary": "AWIの要約"},
    ]
    assert not captured.err


@pytest.mark.parametrize("other_output", ["--count", "--jsonl"])
def test_list_summary_only_conflicts_with_other_output_options(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    other_output: str,
) -> None:
    """要約出力と他の出力形式の同時指定を引数解析で拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "list", "--summary-only", other_output], home=tmp_path)

    assert exc_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("legacy_option", ["--json", "--no-json"])
def test_list_rejects_legacy_json_option_names(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    legacy_option: str,
) -> None:
    """旧名は出力形式を誤読させるため、新名の省略形としても受理しない。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "list", legacy_option], home=tmp_path)

    assert exc_info.value.code == 2
    assert legacy_option in capsys.readouterr().err
