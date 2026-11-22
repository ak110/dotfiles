# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

import pathlib
import re
import typing

import pytest
from agent_toolkit._testing.selection_check_support import (
    _DOTFILES_PYPROJECT,
    _READ_REQUEST,
    _awi,
    _dispatch,
    _norm_spec_selection,
    _write_files,
    _write_selection,
)
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (None, 1),
        ("なし", 1),
        ("", 1),
        (f"agent-toolkit/skills/atk-mode/roles/exec.parent.md。{_READ_REQUEST}", 1),
        (f"agent-toolkit/skills/atk-mode/roles/exec.parent.md、.claude/skills/edit/SKILL.md。{_READ_REQUEST}", 0),
    ],
    ids=["omitted", "none", "empty", "partial", "complete"],
)
def test_public_command_requires_project_norm_spec(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    spec: str | None,
    expected: int,
) -> None:
    repo, notes = env
    (repo / "pyproject.toml").write_text(_DOTFILES_PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")
    _write_files(repo, "agent-toolkit/skills/atk-mode/roles/exec.parent.md", ".claude/skills/edit/SKILL.md", "scripts/tool.py")
    _awi(notes, "norm.md", "`agent-toolkit/skills/atk-mode/roles/exec.parent.md`と`.claude/skills/edit/SKILL.md`を変える。")
    _awi(notes, "code.md", "`scripts/tool.py`を変える。")
    decisions: list[dict[str, typing.Any]] = [
        {
            "WI": "norm.md",
            "レーン": "lane-01",
            "書込対象": ["agent-toolkit/skills/atk-mode/roles/exec.parent.md", ".claude/skills/edit/SKILL.md"],
        },
        {"WI": "code.md", "レーン": "lane-02", "書込対象": ["scripts/tool.py"]},
    ]
    selection = _norm_spec_selection(tmp_path, decisions, {"norm.md": spec})

    for _ in range(2):
        assert _dispatch("--work-dir", str(repo), str(selection)) == expected
        lines = [
            line for line in capsys.readouterr().err.splitlines() if re.match(r"\S+\.md: プロジェクト規範の指定の不足", line)
        ]
        assert all(line.startswith("norm.md: ") for line in lines)
        if expected:
            assert lines
            if spec and spec != "なし":
                assert lines == [
                    "norm.md: プロジェクト規範の指定の不足: 規範変更の対象ファイル: 欠けた記載: .claude/skills/edit/SKILL.md"
                ]
        else:
            assert not lines


def test_agent_doc_reflection_requires_read_request(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, notes = env
    (repo / "pyproject.toml").write_text(_DOTFILES_PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")
    original = ".chezmoi-source/dot_claude/rules/personal.md"
    _write_files(repo, original)
    _awi(notes, "rules.md", f"`{original}`を変える。")
    decisions: list[dict[str, typing.Any]] = [{"WI": "rules.md", "レーン": "lane-01", "書込対象": [original]}]
    _norm_spec_selection(tmp_path, decisions, {"rules.md": original})
    assert _dispatch("--work-dir", str(repo), str(tmp_path / "selection.yaml")) == 1
    error = capsys.readouterr().err
    assert "rules.md: プロジェクト規範の指定の不足: 方針と障害記録を読む要求: 欠けた記載: " in error
    decisions = [{"WI": "rules.md", "レーン": "lane-01", "書込対象": [original]}]
    _norm_spec_selection(tmp_path, decisions, {"rules.md": f"{original}。{_READ_REQUEST}"})
    assert _dispatch("--work-dir", str(repo), str(tmp_path / "selection.yaml")) == 0, capsys.readouterr().err


@pytest.mark.parametrize("config", ["absent", "custom"])
def test_project_norm_spec_follows_repository_config(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], config: str
) -> None:
    repo, notes = env
    if config == "custom":
        (repo / "pyproject.toml").write_text(
            '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "設計記録"\npaths = ["docs/"]\n'
            'require-text = ["独自の要求"]\n',
            encoding="utf-8",
        )
    _awi(notes, "doc.md", "`docs/development/design.md`と`AGENTS.md`を変える。")
    _write_files(repo, "AGENTS.md")
    decisions: list[dict[str, typing.Any]] = [
        {"WI": "doc.md", "レーン": "lane-01", "書込対象": ["docs/development/design.md", "AGENTS.md"]}
    ]
    _norm_spec_selection(tmp_path, decisions, {})
    expected = 1 if config == "custom" else 0
    assert _dispatch("--work-dir", str(repo), str(tmp_path / "selection.yaml")) == expected
    error = capsys.readouterr().err
    if expected:
        assert "doc.md: プロジェクト規範の指定の不足: 設計記録（docs/development/design.mdが当たる）" in error
        decisions = [{"WI": "doc.md", "レーン": "lane-01", "書込対象": ["docs/development/design.md", "AGENTS.md"]}]
        _norm_spec_selection(tmp_path, decisions, {"doc.md": "独自の要求"})
        assert _dispatch("--work-dir", str(repo), str(tmp_path / "selection.yaml")) == 0, capsys.readouterr().err
    else:
        assert error == ""


@pytest.mark.parametrize(
    "content",
    [
        "[tool.agent-toolkit.pick-wi-check\n",
        '[tool.agent-toolkit.pick-wi-check]\nnorm-spec = "規範"\n',
        '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "x"\npaths = "AGENTS.md"\nrequire-paths = true\n',
        '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "x"\npaths = ["AGENTS.md"]\n',
        '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "x"\nagent-doc = true\nrequire-paths = true\nunknown = 1\n',
    ],
    ids=["syntax", "not-array", "paths-type", "no-requirement", "unknown-key"],
)
def test_invalid_project_norm_config_is_input_error(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], content: str
) -> None:
    repo, notes = env
    (repo / "pyproject.toml").write_text(content, encoding="utf-8")
    _awi(notes, "a.md", "`src/model.py`を変える。")
    selection = _write_selection(
        tmp_path / "selection.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}]
    )
    assert _dispatch("--work-dir", str(repo), str(selection)) == 2
    error = capsys.readouterr().err
    assert str(repo / "pyproject.toml") in error and "次の操作: " in error


__all__ = ["fixture_env"]
