"""WIのファイル名の検証、比較キー、依存先の警告と補完候補のテスト。"""

import argparse
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import constants as _wi_constants
from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._testing.wi_entry_files import write_awi, write_uwi


def test_case_sensitivity_probe_reports_linux_filesystem_as_case_sensitive(tmp_path: pathlib.Path) -> None:
    """実際のプローブ処理が一時ディレクトリを大文字小文字を区別すると判定し、残留物を残さない。"""
    assert _wi_filenames.is_case_sensitive(tmp_path) is True
    assert not list(tmp_path.iterdir())


def test_case_sensitivity_probe_detects_case_insensitive_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """名前を畳み込むディレクトリでは、反転名が存在することから区別しないと判定する。"""
    original_exists = pathlib.Path.exists

    def case_folding_exists(self: pathlib.Path) -> bool:
        """名前の大文字小文字を無視して実在判定するファイルシステムを模擬する。"""
        if original_exists(self):
            return True
        return any(entry.name.lower() == self.name.lower() for entry in self.parent.iterdir())

    monkeypatch.setattr(pathlib.Path, "exists", case_folding_exists)

    assert _wi_filenames.is_case_sensitive(tmp_path) is False
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("case_sensitive", "expected"),
    [(True, False), (False, True)],
)
def test_comparison_key_folds_case_only_when_insensitive(case_sensitive: bool, expected: bool) -> None:
    """比較キーは大文字小文字を区別しない場合だけ同名として畳み込む。"""
    left = _wi_filenames.comparison_key("Same.md", case_sensitive=case_sensitive)
    right = _wi_filenames.comparison_key("same.md", case_sensitive=case_sensitive)

    assert (left == right) is expected


@pytest.mark.parametrize("case_sensitive", [True, False])
def test_missing_dependency_warnings_reports_only_unresolvable_references(case_sensitive: bool) -> None:
    """取り込み先に実在しない参照だけを警告へ列挙し、比較キーで一致する参照は除く。"""
    warnings = _wi_filenames.missing_dependency_warnings(
        [("a.md", "Present.md"), ("a.md", "absent.md")],
        resolvable={"present.md"},
        case_sensitive=case_sensitive,
    )

    expected = ["a.mdのdepends_onが参照するPresent.mdは取り込み先に実在しません"] if case_sensitive else []
    assert warnings == [*expected, "a.mdのdepends_onが参照するabsent.mdは取り込み先に実在しません"]


def test_make_filename_completer_limits_states(tmp_path: pathlib.Path) -> None:
    """指定した状態のファイルだけを候補として返す。"""
    private_notes = tmp_path / "private-notes"
    write_awi(private_notes, "inbox.md", state="inbox")
    write_awi(private_notes, "processing.md", state="processing")
    completer = _wi_filenames.make_filename_completer((_wi_constants.WI_STATE_INBOX,))
    assert completer("") == ["inbox.md"]


def test_make_filename_completer_filters_entry_type(tmp_path: pathlib.Path) -> None:
    """種別を指定した場合はfrontmatterの種別が一致するものだけを返す。"""
    private_notes = tmp_path / "private-notes"
    write_awi(private_notes, "awi.md")
    write_uwi(private_notes, "uwi.md")
    completer = _wi_filenames.make_filename_completer(_wi_constants.WI_ACTIVE_STATES, _wi_constants.WI_TYPE_UWI)
    assert completer("") == ["uwi.md"]


def test_make_filename_completer_matches_prefix_and_sorts(tmp_path: pathlib.Path) -> None:
    """prefix一致で限定し、結果をソートして返す。"""
    private_notes = tmp_path / "private-notes"
    write_awi(private_notes, "pre-z.md")
    write_awi(private_notes, "pre-a.md")
    write_awi(private_notes, "other.md")
    completer = _wi_filenames.make_filename_completer(_wi_constants.WI_ACTIVE_STATES)
    assert completer("pre-") == ["pre-a.md", "pre-z.md"]


def _positional_completer(command: str, dest: str) -> Any:
    """`atk wi <command>`の位置引数へ実際に設定された補完関数を返す。"""
    parser = atk._build_parser()  # pylint: disable=protected-access
    wi_parser = next(
        action.choices["wi"]
        for action in parser._actions  # pylint: disable=protected-access
        if isinstance(action, argparse._SubParsersAction)  # pylint: disable=protected-access
    )
    command_parser = next(
        action.choices[command]
        for action in wi_parser._actions  # pylint: disable=protected-access
        if isinstance(action, argparse._SubParsersAction)  # pylint: disable=protected-access
    )
    return next(action.completer for action in command_parser._actions if action.dest == dest)  # pylint: disable=protected-access


@pytest.mark.parametrize(
    ("command", "dest", "expected"),
    [
        ("adopt", "filenames", ["hold-awi.md", "hold-uwi.md", "inbox-awi.md", "inbox-uwi.md", "processing-awi.md"]),
        ("reject", "filenames", ["hold-awi.md", "hold-uwi.md", "inbox-awi.md", "inbox-uwi.md", "processing-awi.md"]),
        ("set-dependencies", "filename", ["hold-awi.md", "hold-uwi.md", "inbox-awi.md", "inbox-uwi.md", "processing-awi.md"]),
        ("answer", "filename", ["hold-uwi.md", "inbox-uwi.md"]),
    ],
)
def test_filename_completion_includes_hold_entries(
    tmp_path: pathlib.Path,
    command: str,
    dest: str,
    expected: list[str],
) -> None:
    """holdを受け付けるサブコマンドの補完は、holdの項目を候補に含める。

    `answer`はUWIだけを候補にする限定を保ち、終端状態の項目は候補に含めない。
    """
    private_notes = tmp_path / "private-notes"
    write_awi(private_notes, "inbox-awi.md", state="inbox")
    write_awi(private_notes, "processing-awi.md", state="processing")
    write_awi(private_notes, "hold-awi.md", state="hold")
    write_uwi(private_notes, "inbox-uwi.md")
    write_uwi(private_notes, "hold-uwi.md")
    (private_notes / "hold").mkdir(exist_ok=True)
    (private_notes / "inbox" / "hold-uwi.md").replace(private_notes / "hold" / "hold-uwi.md")
    write_awi(private_notes, "adopted-awi.md", state="adopted")
    assert _positional_completer(command, dest)("") == expected


class TestValidateFilename:
    """`_validate_filename`の拡張子`.md`省略入力の正規化を検証する（fb 20260721-164301-001反映）。"""

    def test_appends_md_extension_when_missing(self, tmp_path: pathlib.Path) -> None:
        """拡張子.md省略入力は正規形へ補完される。"""
        (tmp_path / "20260721-160220-001.md").write_text("dummy", encoding="utf-8")
        path = _wi_filenames.validate_filename("20260721-160220-001", tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert path == tmp_path / "20260721-160220-001.md"

    def test_preserves_md_extension_when_present(self, tmp_path: pathlib.Path) -> None:
        """拡張子.md付き入力は従来どおり解決される（後方互換）。"""
        (tmp_path / "20260721-160220-001.md").write_text("dummy", encoding="utf-8")
        path = _wi_filenames.validate_filename("20260721-160220-001.md", tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert path == tmp_path / "20260721-160220-001.md"
