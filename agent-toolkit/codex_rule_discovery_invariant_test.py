"""規範のシェル例が候補の選択と部分失敗時の継続を保つことを確かめる。"""

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).parent / "share/rules/rules-common.codex.md"

# 例はPOSIX shの書式で、権限の拒否もPOSIXのファイル権限で再現する。
pytestmark = pytest.mark.skipif(os.name == "nt", reason="規範の例はPOSIX shで実行する")


def _run(index: int, home: Path, project: Path) -> subprocess.CompletedProcess[str]:
    text = SOURCE.read_text(encoding="utf-8")
    section = text.split("## Claude Code向けに配置された規範の読込\n", 1)[1].split("\n## ", 1)[0]
    script = re.findall(r"```sh\n(.*?)\n```", section, re.DOTALL)[index]
    if index:
        script = re.sub(r"^project=.*$", f"project={shlex.quote(str(project))}", script, flags=re.MULTILINE)
        script = re.sub(r"^current=.*$", f"current={shlex.quote(str(project))}", script, flags=re.MULTILINE)
    return subprocess.run(
        ["/bin/sh"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "HOME": str(home)},
        timeout=10,
    )


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("規範\n", encoding="utf-8")
    return path


def _project(tmp_path: Path) -> tuple[Path, set[str]]:
    project = tmp_path / "parent/project"
    expected = {
        str(_file(tmp_path / "parent/CLAUDE.local.md")),
        str(_file(project / "CLAUDE.md")),
        str(_file(project / ".agents/skills/example/SKILL.md")),
        str(_file(project / ".claude/rules/example.md")),
    }
    return project, expected


def _permissions() -> None:
    if os.geteuid() == 0:
        pytest.skip("rootではchmod 000による読取・検索の拒否を再現できない")


@pytest.mark.parametrize("target", [False, True])
def test_ancestor_link_failure_continues(tmp_path: Path, target: bool) -> None:
    _permissions()
    project, expected = _project(tmp_path)
    secret = _file(tmp_path / "unreadable")
    secret.chmod(0)
    link = project.parent / ("CLAUDE.local.md" if target else "unrelated")
    if target:
        link.unlink()
        expected.remove(str(link))
    link.symlink_to(secret)
    try:
        result = _run(1, tmp_path, project)
        assert result.returncode != 0 if target else result.returncode == 0
        assert bool(result.stderr) == target
        assert expected <= set(result.stdout.splitlines())
    finally:
        secret.chmod(0o600)


def test_skills_failure_does_not_hide_rules(tmp_path: Path) -> None:
    _permissions()
    project, expected = _project(tmp_path)
    blocked = project / ".agents/skills/blocked"
    blocked.mkdir()
    blocked.chmod(0)
    try:
        result = _run(1, tmp_path, project)
        assert result.returncode != 0
        assert result.stderr
        assert expected <= set(result.stdout.splitlines())
    finally:
        blocked.chmod(0o700)


def test_user_candidates_ignore_unrelated_entries(tmp_path: Path) -> None:
    _permissions()
    claude = tmp_path / ".claude"
    expected = {str(_file(claude / "CLAUDE.md")), str(_file(claude / "rules/host.local.md"))}
    secret = _file(tmp_path / "unreadable")
    secret.chmod(0)
    (claude / "unrelated").symlink_to(secret)
    blocked = claude / "blocked"
    blocked.mkdir()
    blocked.chmod(0)
    _file(claude / "rules/ignored.md")
    try:
        result = _run(0, tmp_path, tmp_path)
        assert result.returncode == 0, result.stderr
        assert set(result.stdout.splitlines()) == expected
    finally:
        secret.chmod(0o600)
        blocked.chmod(0o700)


def test_candidate_set_and_agents_exclusion(tmp_path: Path) -> None:
    project, expected = _project(tmp_path)
    _file(project.parent / "AGENTS.md")
    excluded = _file(project.parent / "CLAUDE.md")
    linked = project / "CLAUDE.local.md"
    linked.symlink_to(_file(tmp_path / "reference"))
    expected.add(str(linked))
    result = _run(1, tmp_path, project)
    assert result.returncode == 0, result.stderr
    assert expected <= set(result.stdout.splitlines())
    assert str(excluded) not in result.stdout.splitlines()
    assert str(tmp_path / "CLAUDE.md") not in result.stdout.splitlines()


@pytest.mark.parametrize("readable", [False, True])
def test_user_rules_listing_failure_is_not_absence(tmp_path: Path, readable: bool) -> None:
    _permissions()
    claude = tmp_path / ".claude"
    main = _file(claude / "CLAUDE.md")
    rule = _file(claude / "rules/host.local.md")
    rule.parent.chmod(0o500 if readable else 0o100)
    try:
        result = _run(0, tmp_path, tmp_path)
        assert (result.returncode == 0) is readable
        assert str(main) in result.stdout.splitlines()
        if readable:
            assert str(rule) in result.stdout.splitlines()
        else:
            assert "権限を確認" in result.stderr and "メインはユーザーへ報告" in result.stderr
    finally:
        rule.parent.chmod(0o700)
