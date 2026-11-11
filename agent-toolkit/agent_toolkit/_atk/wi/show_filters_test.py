"""WIのshow: filtersのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit.atk_test import _FIXED_TIMESTAMP, _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


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
            atk.main(["wi", "show"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "使い方: atk wi show" in captured.err
        assert "FILENAME" in captured.err
        assert "--all" in captured.err
        error_line = captured.err.rstrip("\n").splitlines()[-1]
        assert "表示するファイル名または--allを指定してください" in error_line


class TestShowTypeFilter:
    """showサブコマンド: --typeでFILENAME探索対象種別を限定する（探索範囲は5状態フォルダ全体）。"""

    def test_type_uwi_finds_uwi_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=uwi指定時はinboxのみを探索しstatusラベル付きで出力する。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", f"{_FIXED_TIMESTAMP}-001.md", "--type=uwi"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"### {_FIXED_TIMESTAMP}-001.md [inbox/unanswered]" in captured.out

    def test_type_awi_excludes_uwi_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--type=awi指定時はuwiエントリを種別不一致として除外し該当なしでexit 2になる。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", f"{_FIXED_TIMESTAMP}-001.md", "--type=awi"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "年月配置に存在しない" in captured.err

    def test_type_all_searches_awi_then_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--typeを省略してallを使う場合は種別を問わず探索し該当エントリを表示する。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

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
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1", source="session-review")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/foo", body="本文2", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all", "--source=session-review"], home=tmp_path)

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
        _write_awi_file(notes, "fb-001.md", source="session-review")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "fb-001.md", "--source=!session-review"], home=tmp_path)

        assert exc_info.value.code == 2

    def test_all_filter_matches_exact_source_for_uwi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all --source=NAME指定時、uwi側も同一sourceのエントリのみ出力される。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, "uwi-001.md", question="質問1", source="session-review")
        _write_uwi_file(notes, "uwi-002.md", question="質問2", source=None)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all", "--type=uwi", "--status=all", "--source=session-review"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "質問1" in captured.out
        assert "質問2" not in captured.out


@pytest.mark.parametrize(
    ("important", "expected"),
    [("all", {"important.md", "normal.md"}), ("yes", {"important.md"}), ("no", {"normal.md"})],
)
def test_show_filters_important_entries(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    important: str,
    expected: set[str],
) -> None:
    """年月配置の重要項目と通常項目を公開フィルターで選択する。"""
    notes = _setup_notes(tmp_path)
    marked = _write_awi_file(notes, "important.md")
    marked.write_text(marked.read_text(encoding="utf-8").replace("type: awi", "important: true\ntype: awi"), encoding="utf-8")
    _write_awi_file(notes, "normal.md")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "show", "--all", "--skip-pull", f"--important={important}"], home=tmp_path)
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert {line.split()[1] for line in output.splitlines() if line.startswith("### ")} == expected


class TestShowStatusFilter:
    """showサブコマンド: --answeredでUWIの回答状況を限定する（--all分岐のみ有効）。"""

    def test_all_answered_excludes_unanswered_uwi_and_awi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--all --answered=yesは回答済みUWIだけを表示する。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "show", "--all", "--answered=yes"], home=tmp_path)

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
        """FILENAME指定時は--answeredを迂回し、未回答uwiでも--answered=yesと無関係に表示される。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "show", f"{_FIXED_TIMESTAMP}-001.md", "--answered=yes"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert f"### {_FIXED_TIMESTAMP}-001.md [inbox/unanswered]" in captured.out
