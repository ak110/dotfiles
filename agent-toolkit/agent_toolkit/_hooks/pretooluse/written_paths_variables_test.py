"""書込先の表記と代入の作用範囲による判定の差を検出する。"""

import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import operation_skills, read_delivery_matching


@pytest.mark.parametrize("suffix", ["setup.sh", "note.md", "app.py"])
@pytest.mark.parametrize("reference", ["$T", "${T}"])
@pytest.mark.parametrize(
    "command",
    ["cat > {path} <<'EOF'\n本文\nEOF\n", "tee {path} <<'EOF'\n本文\nEOF\n", "sed -i 's/a/b/' {path}"],
)
def test_variable_write_matches_literal(tmp_path: pathlib.Path, suffix: str, reference: str, command: str) -> None:
    """同じ書込の固定パスと変数参照は同じ書込先を返す。"""
    target = tmp_path / suffix
    target.write_text("a", encoding="utf-8")
    literal = {"command": command.format(path=str(target))}
    variable = {"command": f"T={tmp_path}; " + command.format(path=f"{reference}/{suffix}")}
    assert operation_skills.written_paths("Bash", variable, "") == [str(target)]
    assert operation_skills.written_paths("Bash", variable, "") == operation_skills.written_paths("Bash", literal, "")
    for operation in ("body-writing", "existing-file-edit"):
        assert operation_skills.operation_matches(operation, "Bash", variable) == operation_skills.operation_matches(
            operation, "Bash", literal
        )
    assert read_delivery_matching.matches("path:*.py", "Bash", variable, "") == (suffix == "app.py")


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
    """未代入・動的な値・外へ作用しない代入を解決済みにしない。"""
    assert not operation_skills.written_paths("Bash", {"command": prefix + "cat > $T/app.py <<'EOF'\nx\nEOF\n"}, "")
