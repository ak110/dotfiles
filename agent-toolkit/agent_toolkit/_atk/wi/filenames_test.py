"""WIのファイル名の検証、比較キー、依存先の警告と補完候補のテスト。"""

import pathlib

import pytest

from agent_toolkit._atk.wi import filenames as _wi_filenames


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


class TestValidateFilename:
    """`validate_filename`の拡張子`.md`省略入力の正規化を検証する（fb 20260721-164301-001反映）。"""

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
