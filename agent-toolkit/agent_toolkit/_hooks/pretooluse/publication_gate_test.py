import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit._hooks import progress_self_check, work_contract
from agent_toolkit._hooks.pretooluse import publication_gate
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


def test_publication_blocks_on_uwis_answered_after_the_baseline(tmp_path, monkeypatch):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repository = config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "snapshot-answered")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "snapshot-answered")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    names = ("20261124-061940-001.md", "20261124-061940-002.md")
    for name in names:
        _write_uwi_file(notes, name, answer="")
    with pytest.raises(SystemExit) as invocation:
        atk.main(
            [
                "wi",
                "list",
                "--skip-pull",
                "--count",
                "--configured-repos",
                "--snapshot-output",
                str(tmp_path / "baseline.json"),
            ],
            home=tmp_path,
        )
    assert invocation.value.code == 0
    payload = {"session_id": "snapshot-answered", "cwd": str(tmp_path)}
    for name in names:
        _write_uwi_file(notes, name, answer="回答済み")

    def gate(command: str) -> str | None:
        denied = publication_gate.check(payload, "snapshot-answered", "Bash", {"command": command})
        return denied[0] if denied else None

    for command in ("git push origin develop", "gh pr create --base master --head develop"):
        denied = gate(command)
        assert denied is not None and all(name in denied for name in names)
    assert gate("git push --dry-run origin develop") is None
    assert gate("git push origin develop") is None
    _write_uwi_file(notes, "20261124-061940-003.md", answer="")
    _write_uwi_file(notes, "20261124-061940-003.md", answer="回答済み")
    changed = gate("git push origin develop")
    assert changed is not None and "20261124-061940-003.md" in changed
    _write_uwi_file(notes, "20261124-061940-003.md", answer="")
    contract = tmp_path / "work-contract.md"
    monkeypatch.setattr(work_contract, "record_path", lambda _sid: contract)
    contract.write_text(f"- 新着WIの判定: {names[0]} | 次回: 別ホストで観測する\n", encoding="utf-8")
    pending = gate("git push")
    assert pending is not None and names[0] not in pending and names[1] in pending
    _write_uwi_file(notes, names[1], answer="")
    assert gate("git push") is None
    _write_uwi_file(notes, names[0], answer="")
    assert gate("git push") is None
    _write_uwi_file(notes, names[1], answer="回答済み")
    recurred = gate("git push")
    assert recurred is not None and names[1] in recurred


def test_own_processing_wis_leave_progress_notice_and_publication_gate(tmp_path, monkeypatch):
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    repo = "github.com/example/foo"
    repository = config.ConfiguredRepository(repo, tmp_path, tmp_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    session = "processing-new-wis"
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)

    def run(*args: str) -> None:
        with pytest.raises(SystemExit) as result:
            atk.main(["wi", *args], home=tmp_path)
        assert result.value.code == 0

    run("list", "--skip-pull", "--count", "--configured-repos", "--snapshot-output", str(tmp_path / "baseline.json"))
    names = tuple(f"20261124-085010-00{number}.md" for number in range(1, 4))
    for name in names:
        _write_awi_file(notes, name)
    run("start-processing", *names[:2], f"--target-repo={repo}")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "direct-delegate")
    run("start-processing", names[2], f"--target-repo={repo}")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)

    payload = {"session_id": session, "cwd": str(tmp_path)}

    def gate(command: str):
        return publication_gate.check(payload, session, "Bash", {"command": command})

    assert gate("git push origin develop") is None
    assert gate("gh pr create --base master --head develop") is None

    outside = "20261124-085011-001.md"
    _write_awi_file(notes, outside)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "other-session")
    run("start-processing", outside, f"--target-repo={repo}")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    run("return-to-inbox", names[2], f"--target-repo={repo}")

    pending = gate("git push origin develop")
    assert pending is not None and outside in pending[0] and names[2] in pending[0]
    assert all(name not in pending[0] for name in names[:2])
    assert progress_self_check.claim_notice(payload, now=1.0) is None
    try:
        notice = progress_self_check.claim_notice(payload, now=1801.0)
        assert notice is not None and outside in notice and names[2] in notice
        assert all(name not in notice for name in names[:2])
    finally:
        progress_self_check.release_reservations()
