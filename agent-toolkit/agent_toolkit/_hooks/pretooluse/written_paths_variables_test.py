"""書込先の表記と代入の作用範囲による判定の差を検出する。"""

import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import operation_skills


@pytest.mark.parametrize("suffix", ["setup.sh", "note.md", "app.py"])
@pytest.mark.parametrize("reference", ["$T", "${T}"])
@pytest.mark.parametrize(
    "command",
    ["cat > {path} <<'EOF'\n本文\nEOF\n", "tee {path} <<'EOF'\n本文\nEOF\n", "sed -i 's/a/b/' {path}"],
)
def test_variable_write_matches_literal(tmp_path: pathlib.Path, suffix: str, reference: str, command: str) -> None:
    """表記と一時領域かどうかによらず、確定した書込先を返す。  pyfltr: allow-comment"""
    temporary = tmp_path / "managed-temp"
    directory = tmp_path / "repo" if suffix == "app.py" else temporary / "session-test"
    directory.mkdir(parents=True)
    target = directory / suffix
    target.write_text("a", encoding="utf-8")
    literal = {"command": command.format(path=str(target))}
    variable = {"command": f"T={directory}; " + command.format(path=f"{reference}/{suffix}")}
    assert operation_skills.written_paths("Bash", variable, "") == [str(target)]
    assert operation_skills.written_paths("Bash", variable, "") == operation_skills.written_paths("Bash", literal, "")


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "T=$(pwd); ",
        "T=$UNSET; ",
        "T=/repo cat /dev/null; ",
        "(T=/repo); ",
        "false || T=/repo; ",
        "if true; then T=/repo; fi; ",
        "T=/repo | cat; ",
    ],
)
def test_unresolved_assignment_is_not_a_known_path(prefix: str) -> None:
    assert not operation_skills.written_paths("Bash", {"command": prefix + "cat > $T/app.py <<'EOF'\nx\nEOF\n"}, "")
