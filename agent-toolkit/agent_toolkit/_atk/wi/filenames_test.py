import pathlib

import pytest

from agent_toolkit._atk.wi import filenames as _wi_filenames


def test_case_sensitivity_probe_reports_linux_filesystem_as_case_sensitive(tmp_path: pathlib.Path) -> None:
    assert _wi_filenames.is_case_sensitive(tmp_path) is True
    assert not list(tmp_path.iterdir())


def test_case_sensitivity_probe_detects_case_insensitive_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_exists = pathlib.Path.exists

    def case_folding_exists(self: pathlib.Path) -> bool:
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
    left = _wi_filenames.comparison_key("Same.md", case_sensitive=case_sensitive)
    right = _wi_filenames.comparison_key("same.md", case_sensitive=case_sensitive)

    assert (left == right) is expected


@pytest.mark.parametrize("case_sensitive", [True, False])
def test_missing_dependency_warnings_reports_only_unresolvable_references(case_sensitive: bool) -> None:
    warnings = _wi_filenames.missing_dependency_warnings(
        [("a.md", "Present.md"), ("a.md", "absent.md")],
        resolvable={"present.md"},
        case_sensitive=case_sensitive,
    )

    expected = ["a.mdのdepends_onが参照するPresent.mdは取り込み先に実在しません"] if case_sensitive else []
    assert warnings == [*expected, "a.mdのdepends_onが参照するabsent.mdは取り込み先に実在しません"]


class TestValidateFilename:
    def test_appends_md_extension_when_missing(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "20260721-160220-001.md").write_text("dummy", encoding="utf-8")
        path = _wi_filenames.validate_filename("20260721-160220-001", tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert path == tmp_path / "20260721-160220-001.md"

    def test_preserves_md_extension_when_present(self, tmp_path: pathlib.Path) -> None:
        """拡張子.md付き入力は従来どおり解決される（後方互換）。"""
        (tmp_path / "20260721-160220-001.md").write_text("dummy", encoding="utf-8")
        path = _wi_filenames.validate_filename("20260721-160220-001.md", tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert path == tmp_path / "20260721-160220-001.md"
