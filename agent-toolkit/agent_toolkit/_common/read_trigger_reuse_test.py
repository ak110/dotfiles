"""不変な宣言行を複数の消費側が参照するときの再利用契約。"""

import pathlib

from agent_toolkit._common import read_triggers


def test_targets_preserve_declared_paths_and_headings(tmp_path: pathlib.Path):
    paths = (tmp_path / "one.md", tmp_path / "two.md")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Do",), paths, ("見出し", ""))
    first = row.targets
    assert [(target.path, target.heading) for target in first] == [(paths[0], "見出し"), (paths[1], "")]
    assert tuple(str(target) for target in row.targets) == (str(paths[0]) + "#見出し", str(paths[1]))
