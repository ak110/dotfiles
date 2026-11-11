# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのlisting: statesのテスト。"""

import datetime
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _FIXED_TIMESTAMP, _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


class TestListStatusFilter:
    """listサブコマンド: --answeredでuwi側のみ回答状況を限定する。"""

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
        """--answered=yes/noが回答状況と一致するUWIだけを出力する。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", f"--answered={answered}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-{expected_suffix}" in captured.out
        assert f"{_FIXED_TIMESTAMP}-{excluded_suffix}" not in captured.out

    def test_active_covers_hold_for_both_types(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=activeがawiとUWIのどちらでもholdを含む3状態を返す。"""
        notes = _setup_notes(tmp_path)
        hold_dir = entry_path(notes, "fb-hold.md", fallback_date=datetime.date(2026, 10, 1)).parent
        hold_dir.mkdir(parents=True, exist_ok=True)
        (hold_dir / "fb-hold.md").write_text(
            "---\nstate: hold\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\nhold本文\n",
            encoding="utf-8",
        )
        (hold_dir / "uwi-hold.md").write_text(
            "---\nstate: hold\ntype: uwi\ntarget_repo: github.com/example/foo\n---\n\n## 質問\n\nhold質問\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--status=active", "--no-jsonl"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fb-hold.md" in captured.out
        assert "uwi-hold.md" in captured.out

    def test_status_all_outputs_every_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=all指定時に全UWIが出力される。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"{_FIXED_TIMESTAMP}-001.md" in captured.out
        assert f"{_FIXED_TIMESTAMP}-002.md" in captured.out

    def test_status_answered_does_not_affect_awi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--answered=yes指定時に回答概念を持たないAWIは除外される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", body="本文1")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--answered=yes"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "# awi" not in captured.out
        assert f"{_FIXED_TIMESTAMP}-001.md" not in captured.out

    def test_status_invalid_choice_exits_2(self, tmp_path: pathlib.Path) -> None:
        """--statusに不正値を指定するとargparseがexit 2で終了する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--status=invalid"], home=tmp_path)

        assert exc_info.value.code == 2

    @pytest.mark.parametrize("status", ["planning", "editing"])
    def test_status_rejects_withdrawn_states(self, tmp_path: pathlib.Path, status: str) -> None:
        """廃止した状態名を--statusへ渡すとargparseがexit 2で終了する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", f"--status={status}"], home=tmp_path)

        assert exc_info.value.code == 2

    @pytest.mark.parametrize(
        "argv",
        [["wi", "list"], ["wi", "show"], ["wi", "grep", "pattern"]],
        ids=["list", "show", "grep"],
    )
    def test_state_and_status_resolve_to_the_same_filter(self, argv: list[str]) -> None:
        """状態フィルターを`--state`と`--status`のどちらの綴りでも同じ値へ解決する。

        キューの語彙は状態ディレクトリ、`WI_STATES`、`wi list --jsonl`の`state`のいずれも`state`であり、
        `atk wi return-to-inbox`と`atk wi rm`も`--state`を受理する。
        フィルター側が`--status`だけを受理すると、語彙どおりに組み立てた実行が終了コード2で終わる。
        """
        parser = atk._build_parser()  # noqa: SLF001  # pylint: disable=protected-access

        by_state = parser.parse_args([*argv, "--state=processable"])
        by_status = parser.parse_args([*argv, "--status=processable"])

        assert by_state.status == by_status.status
        assert "processable" in by_state.status
