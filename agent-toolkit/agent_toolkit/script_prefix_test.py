import pathlib

from agent_toolkit import script_prefix_invariant_test as checks


def test_test_only_import_does_not_make_entry_private(tmp_path: pathlib.Path) -> None:
    entry = tmp_path / "_foo.py"
    entry.write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    (tmp_path / "_foo_test.py").write_text("import _foo\n", encoding="utf-8")

    assert checks._standalone_private_prefixed_scripts(sorted(tmp_path.glob("*.py"))) == ["_foo.py"]  # pylint: disable=protected-access
