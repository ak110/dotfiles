"""不変な宣言行を複数の消費側が参照するときの再利用契約。"""

import dataclasses
import pathlib

import pytest

from agent_toolkit._common import read_triggers


def test_targets_are_reused_by_the_row_and_new_rows_resolve_again(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    paths = (tmp_path / "one.md", tmp_path / "two.md")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Do",), paths, ("見出し", ""))
    resolved = []
    original = pathlib.Path.resolve

    def resolve(path, *args, **kwargs):
        resolved.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", resolve)
    first = row.targets
    assert [(target.path, target.heading) for target in first] == [(paths[0], "見出し"), (paths[1], "")]
    assert row.targets is first
    assert row.targets[0] in first
    assert tuple(str(target) for target in row.targets) == (str(paths[0]) + "#見出し", str(paths[1]))
    assert resolved == list(paths)
    renewed = dataclasses.replace(row, line=2)
    assert renewed.targets == first
    assert renewed.targets is not first
    assert resolved == [*paths, *paths]
