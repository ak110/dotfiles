from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_toolkit import atk
from agent_toolkit._testing import git_repository


def _run(monkeypatch: pytest.MonkeyPatch, target: Path, *options: str) -> int:
    monkeypatch.chdir(target)
    with pytest.raises(SystemExit) as result:
        atk.main(["setup-project", *options])
    assert isinstance(result.value.code, int)
    return result.value.code


def test_default_migrates_instructions_and_links_skills_without_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project"
    (target / ".claude" / "skills").mkdir(parents=True)
    (target / "CLAUDE.md").write_text("# プロジェクト規範\n", encoding="utf-8")

    assert _run(monkeypatch, target) == 0
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "# プロジェクト規範\n"
    assert not (target / "CLAUDE.md").exists()
    link = target / ".agents" / "skills"
    assert link.resolve() == (target / ".claude" / "skills").resolve()
    if os.name != "nt":
        # プロジェクトを移しても辿れる相対リンクにする。Windowsは絶対パスのジャンクションになる。
        assert os.readlink(link) == "../.claude/skills"
    assert not (target / ".claude" / "rules" / "agent-toolkit").exists()


def test_local_instructions_create_adapter_and_unique_git_exclude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = git_repository.init_repository(tmp_path / "project")
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


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="特権の要らないジャンクションでの共有をWindows実機で確かめる")
@pytest.mark.parametrize("existing", ["missing", "old-symlink", "git-symlink"])
def test_windows_shares_skills_without_symlink_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: str
) -> None:
    """シンボリックリンクの作成特権が無くてもスキルを共有し、以前の版の壊れたリンクを直し、再実行とcleanで同じリンクを扱う。

    Git for Windowsはジャンクションを辿って中身を未追跡に並べるため、共有した中身をGitの対象に出さない。
    """
    target = git_repository.init_repository(tmp_path / "project")
    skill = target / ".claude" / "skills" / "example" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# example\n", encoding="utf-8")
    link = target / ".agents" / "skills"
    if existing != "missing":
        link.parent.mkdir()
        try:
            link.symlink_to("../.claude/skills" if existing == "old-symlink" else "..\\.claude\\skills")
        except OSError as error:
            if getattr(error, "winerror", None) != 1314:
                raise
            pytest.skip("既存のシンボリックリンクを用意する特権が無い")

    def deny_symlink(*_args: object, **_kwargs: object) -> None:

        raise OSError(None, "クライアントは要求された特権を保有していません。", None, 1314)

    monkeypatch.setattr(os, "symlink", deny_symlink)
    assert _run(monkeypatch, target) == 0
    assert link.is_symlink() if existing == "git-symlink" else link.is_junction()
    assert (link / "example" / "SKILL.md").read_text(encoding="utf-8") == "# example\n"
    untracked = git_repository.git_output(target, "status", "--porcelain", "--untracked-files=all").splitlines()
    assert [line for line in untracked if line.startswith("?? .agents/skills/")] == []
    connected = link.lstat().st_ino
    assert _run(monkeypatch, target) == 0
    assert link.lstat().st_ino == connected
    assert _run(monkeypatch, target, "--clean") == 0
    assert not (target / ".agents").exists()
    assert skill.read_text(encoding="utf-8") == "# example\n"
