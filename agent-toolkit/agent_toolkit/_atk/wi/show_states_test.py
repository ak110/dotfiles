"""WIのshow: statesのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.wi_show_support import _write_awi_processing_file, _write_awi_state_file
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


class TestShowProcessing:
    """showサブコマンド: processing状態も探索・走査対象に含める。"""

    def test_single_file_finds_entry_in_processing_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """FILENAME指定時にinboxで見つからずprocessingで見つかる場合、その本文が表示される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_processing_file(notes, "fb-processing.md", body="processing本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-processing.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-processing.md" in captured.out
        assert "processing本文" in captured.out

    def test_single_file_follows_state_change_at_fixed_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同じ年月パスのstateを書き換えた項目を、変更後の状態で表示する。"""
        notes = _setup_notes(tmp_path)
        path = _write_awi_state_file(notes, "inbox", "fb-dup.md", body="保持する本文")
        path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", "state: processing"), encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-dup.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-dup.md [processing]" in captured.out
        assert "保持する本文" in captured.out
        assert entry_path(notes, path.name) == path

    def test_all_scans_active_states_for_both_types(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all指定時にactiveの3状態を型によらず出力する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-inbox.md", body="inbox本文")
        _write_awi_processing_file(notes, "fb-processing.md", body="processing本文")
        _write_awi_state_file(notes, "hold", "fb-hold.md", body="hold本文")
        held_uwi = _write_uwi_file(notes, "uwi-hold.md", question="hold UWI")
        held_uwi.write_text(
            held_uwi.read_text(encoding="utf-8").replace("state: inbox", "state: hold"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-inbox.md" in captured.out
        assert "inbox本文" in captured.out
        assert "### fb-processing.md" in captured.out
        assert "processing本文" in captured.out
        assert "### fb-hold.md" in captured.out
        assert "hold本文" in captured.out
        assert "### uwi-hold.md" in captured.out
        assert "hold UWI" in captured.out


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
        _write_awi_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-adopted.md", "--status=adopted"], home=tmp_path)

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
        _write_awi_state_file(notes, "rejected", "fb-rejected.md", body="rejected本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-rejected.md", "--status=rejected"], home=tmp_path)

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
        """`--status`未指定（省略時はactive）でもFILENAME指定時はadopted配下を探索し表示できる。

        省略時に使う`--status=active`をFILENAME単発指定分岐へ適用すると、`atk wi show <FILENAME>`
        でadopted・rejected状態のエントリを一切参照できなくなるため、単発指定は`--status`を
        迂回する契約になっている。
        """
        notes = _setup_notes(tmp_path)
        _write_awi_state_file(notes, "adopted", "fb-adopted.md", body="adopted本文")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-adopted.md"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "### fb-adopted.md" in captured.out
        assert "adopted本文" in captured.out
