"""Markdown構造検査の公開CLI契約を確認する。"""

import pathlib
import subprocess
import sys
from collections.abc import Callable

import check_markdown_structure
import pyfltr.command.targets
import pyfltr.config.config
import pytest


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("甲。乙。\n", "甲。\n乙。\n"),
        ("- 甲。乙。丙。\n", "- 甲。\n  乙。\n  丙。\n"),
        ("12. 甲。乙。\n", "12. 甲。\n    乙。\n"),
        ("1. - 甲。乙。\n", "1. - 甲。\n     乙。\n"),
        ("- 親\n  - 甲。乙。\n", "- 親\n  - 甲。\n    乙。\n"),
        ("- 親\n\n  甲。乙。\n", "- 親\n\n  甲。\n  乙。\n"),
        ("**甲。**乙。\n", "**甲。**\n乙。\n"),
        ("甲。**乙。**\n", "甲。\n**乙。**\n"),
        ("[甲。](../doc.md#節)乙[^注]。\n", "[甲。](../doc.md#節)\n乙[^注]。\n"),
        ("甲。`乙`。\n", "甲。\n`乙`。\n"),
        ("\\`甲。乙。\\`\n", "\\`甲。\n乙。\n\\`\n"),
        ("\ufeff甲。乙。\r\n", "\ufeff甲。\r\n乙。\r\n"),
    ],
)
def test_fix_preserves_source_and_list_structure(tmp_path: pathlib.Path, source: str, expected: str) -> None:
    path = tmp_path / "doc.md"
    path.write_bytes(source.encode("utf-8"))
    assert check_markdown_structure.main([str(path)]) == 1
    assert path.read_bytes() == source.encode("utf-8")
    assert check_markdown_structure.main(["--fix", str(path)]) == 0
    assert path.read_bytes() == expected.encode("utf-8")
    assert check_markdown_structure.main(["--fix", str(path)]) == 0
    assert path.read_bytes() == expected.encode("utf-8")


@pytest.mark.parametrize(
    "source",
    [
        "本文（補足。説明）。\n次。\n",
        "本文(補足。説明)。\n次。\n",
        "「甲。『乙。丙。』丁。」とする。\n",
        "「甲。\n乙。」とする。\n",
        "`甲。乙。（外（内））`とする。\n",
        "``甲。`乙。（外（内））``とする。\n",
        "`甲。\n乙。（外（内））`とする。\n",
        "```md\n甲。乙。（外（内））\n```\n",
        "    甲。乙。（外（内））\n",
        "<!-- 甲。乙。（外（内）） -->\n本文。\n",
        "---\nname: 甲。乙。（外（内））\n---\n本文。\n",
        "# 見出し。続き。\n",
        "| 見出し |\n| --- |\n| 甲。乙。 |\n",
        "> 甲。乙。（外（内））\n> 引用。続き。\n",
        "逐語引用: 甲。乙。\n",
        "甲。\n乙。\n",
    ],
)
def test_excluded_constructs_are_unchanged(tmp_path: pathlib.Path, source: str) -> None:
    path = tmp_path / "doc.md"
    path.write_text(source, encoding="utf-8")
    assert check_markdown_structure.main(["--fix", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.parametrize(
    ("source", "location"),
    [
        ("本文（外（内））。\n", ":1:5:"),
        ("本文（外\n（内））。\n", ":2:1:"),
        ("| 見出し |\n| --- |\n| 本文（外（内）） |\n", ":3:7:"),
        ("# 本文（外（内））\n", ":1:7:"),
    ],
)
def test_nested_parentheses_are_errors_even_with_fix(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], source: str, location: str
) -> None:
    path = tmp_path / "doc.md"
    path.write_text(source, encoding="utf-8")
    assert check_markdown_structure.main(["--fix", str(path)]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert f"{path}{location} error:" in captured.err
    assert path.read_text(encoding="utf-8") == source


def test_fixture_and_external_exclusions(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    fixture = tmp_path / "fixtures" / "request.md"
    fixture.parent.mkdir()
    fixture.write_text("甲。乙。（外（内））\n", encoding="utf-8")
    external = tmp_path / ".chezmoi-source/dot_gemini/antigravity-cli/skills/japanese-tech-writing/SKILL.md"
    external.parent.mkdir(parents=True)
    external.write_text("甲。乙。\n", encoding="utf-8")
    assert check_markdown_structure.main(["--fix", str(fixture), str(external)]) == 0
    assert fixture.read_text(encoding="utf-8") == "甲。乙。（外（内））\n"
    assert external.read_text(encoding="utf-8") == "甲。乙。\n"
    external.write_text("甲（外（内））。\n", encoding="utf-8")
    assert check_markdown_structure.main([str(external)]) == 1


def test_fixture_named_ancestor_does_not_exclude_project_input(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "fixtures" / "project"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)
    path = project / "doc.md"
    path.write_text("甲。乙。\n", encoding="utf-8")
    assert check_markdown_structure.main([str(path)]) == 1
    assert check_markdown_structure.main(["--fix", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == "甲。\n乙。\n"


def test_input_error_takes_precedence_and_other_files_are_checked(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff")
    violation = tmp_path / "doc.md"
    violation.write_text("甲。乙。\n", encoding="utf-8")
    assert check_markdown_structure.main([str(tmp_path / "missing.md"), str(bad), str(violation), str(tmp_path)]) == 2
    assert violation.read_text(encoding="utf-8") == "甲。乙。\n"
    captured = capsys.readouterr()
    assert not captured.out
    assert f"{violation}:1:2: error:" in captured.err
    assert captured.err.count("入力エラー") == 3
    assert check_markdown_structure.main([]) == 0


def test_process_entrypoint_and_multiple_files(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    good.write_text("甲。\n", encoding="utf-8")
    bad.write_text("乙。丙。\n", encoding="utf-8")
    script = pathlib.Path(check_markdown_structure.__file__)
    result = subprocess.run(
        [sys.executable, str(script), str(good), str(bad)],
        capture_output=True,
        encoding="utf-8",
        env=host_environ(),
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert f"{bad}:1:2: error:" in result.stderr


@pytest.mark.parametrize("second_input", ["missing", "invalid-utf8", "directory"])
def test_fix_prepares_all_inputs_before_writing(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], second_input: str
) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    third = tmp_path / "third.md"
    original = "甲。乙。\n".encode()
    for path in (first, third):
        path.write_bytes(original)
    if second_input == "invalid-utf8":
        second.write_bytes(b"\xff")
    elif second_input == "directory":
        second.mkdir()
    result = subprocess.run(
        [sys.executable, check_markdown_structure.__file__, "--fix", str(first), str(second), str(third)],
        capture_output=True,
        encoding="utf-8",
        env=host_environ(),
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert not result.stdout
    assert str(second) in result.stderr
    assert "入力エラー" in result.stderr
    assert first.read_bytes() == original
    assert third.read_bytes() == original


def test_fix_writes_all_prepared_changes(tmp_path: pathlib.Path) -> None:
    paths = [tmp_path / "first.md", tmp_path / "second.md"]
    for path in paths:
        path.write_text("甲。乙。\n", encoding="utf-8")
    assert check_markdown_structure.main(["--fix", *(str(path) for path in paths)]) == 0
    for path in paths:
        assert path.read_text(encoding="utf-8") == "甲。\n乙。\n"


@pytest.mark.parametrize(
    ("source", "locations"),
    [
        (
            "| 列1 | 列2 |\n| --- | --- |\n| 外（内（補足）） | 正常 |\n\n後続（外（内））。\n",
            [":3:6:", ":5:5:"],
        ),
        ("| 列1 | 列2 |\n| --- | --- |\n| 外（ | 続き |\n\n閉じる）。\n\n後続（正常）。\n", []),
        ("| コード | 本文 |\n| --- | --- |\n| `（外（内））` | 単独（補足） |\n\n後続（正常）。\n", []),
    ],
)
def test_cli_table_positions_and_following_paragraph(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], source: str, locations: list[str]
) -> None:
    path = tmp_path / "table.md"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, check_markdown_structure.__file__, str(path)],
        capture_output=True,
        encoding="utf-8",
        env=host_environ(),
        timeout=10,
        check=False,
    )
    assert result.returncode == int(bool(locations))
    assert not result.stdout
    assert result.stderr.splitlines() == [f"{path}{location} error: 全角丸括弧の入れ子を解消する" for location in locations]
    assert path.read_text(encoding="utf-8") == source


def test_pyfltr_registration_covers_nested_markdown(tmp_path: pathlib.Path) -> None:
    repository = pathlib.Path(__file__).resolve().parents[1]
    config = pyfltr.config.config.load_config(repository, global_config_path=tmp_path / "absent.toml")
    assert not config["dotfiles-markdown-structure-subproject-aware"]
    candidates = [pathlib.Path(p) for p in ("README.md", "a/b/c/d/e.md", ".claude/skills/a/b.md", "a.md.tmpl", "a.py")]
    patterns = config.commands["dotfiles-markdown-structure"].targets
    assert isinstance(patterns, list)
    selected = pyfltr.command.targets.filter_by_globs(candidates, patterns)
    assert selected == candidates[:-1]
