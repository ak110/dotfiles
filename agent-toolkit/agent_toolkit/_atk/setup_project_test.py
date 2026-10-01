"""利用者が使うatk setup-projectコマンドを通して移行・削除を検証する。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_toolkit import atk


def _run(monkeypatch: pytest.MonkeyPatch, target: Path, *options: str) -> int:
    monkeypatch.chdir(target)
    with pytest.raises(SystemExit) as result:
        atk.main(["setup-project", *options])
    assert isinstance(result.value.code, int)
    return result.value.code


def test_default_migrates_instructions_and_links_skills_without_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project"
    (target / ".claude" / "skills").mkdir(parents=True)
    (target / "CLAUDE.md").write_text("# プロジェクト指示\n", encoding="utf-8")

    assert _run(monkeypatch, target) == 0
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "# プロジェクト指示\n"
    assert not (target / "CLAUDE.md").exists()
    assert os.readlink(target / ".agents" / "skills") == "../.claude/skills"
    assert not (target / ".claude" / "rules" / "agent-toolkit").exists()


def test_with_rules_copies_plugin_rules_and_clean_removes_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project"
    (target / ".claude" / "skills").mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "rules"

    assert _run(monkeypatch, target, "--with-rules") == 0
    assert _run(monkeypatch, target, "--with-rules") == 0
    destination = target / ".claude" / "rules" / "agent-toolkit"
    assert {path.name for path in destination.iterdir()} == {path.name for path in source.iterdir()}
    assert (destination / "01-agent.md").read_bytes() == (source / "01-agent.md").read_bytes()
    assert _run(monkeypatch, target, "--clean") == 0
    assert not (target / ".agents").exists()
    assert not (target / ".claude" / "rules").exists()


def test_local_instructions_create_adapter_and_unique_git_exclude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project"
    target.mkdir()
    subprocess.run(["git", "-C", str(target), "init", "-q"], check=True)
    (target / "CLAUDE.md").write_text("# 共有指示\n", encoding="utf-8")
    (target / "CLAUDE.local.md").write_text("# 個人指示\n", encoding="utf-8")

    assert _run(monkeypatch, target) == 0
    assert _run(monkeypatch, target) == 0
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "# 共有指示\n"
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == "# CLAUDE.md\n\n@AGENTS.md\n"
    exclude = (target / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count("/CLAUDE.md") == 1


def test_unexpected_instruction_file_is_left_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project"
    target.mkdir()
    agents = target / "AGENTS.md"
    claude = target / "CLAUDE.md"
    agents.write_text("# 共有指示\n", encoding="utf-8")
    claude.write_text("# 別の指示\n", encoding="utf-8")

    assert _run(monkeypatch, target) == 1
    assert agents.read_text(encoding="utf-8") == "# 共有指示\n"
    assert claude.read_text(encoding="utf-8") == "# 別の指示\n"


def test_clean_rejects_unexpected_skills_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "project"
    (target / ".agents").mkdir(parents=True)
    (target / ".agents" / "skills").symlink_to("../other")

    assert _run(monkeypatch, target, "--clean") == 1
    assert os.readlink(target / ".agents" / "skills") == "../other"
    lines = capsys.readouterr().err.splitlines()
    failure_index = next(index for index, line in enumerate(lines) if line.startswith("失敗: "))
    assert lines[failure_index + 1].startswith("次の操作: ")
    assert "atk setup-project" in lines[failure_index + 1]
