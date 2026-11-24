import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit._hooks import work_contract
from agent_toolkit._hooks.pretooluse import publication_gate
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_uwi_file


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
    contract = tmp_path / "work-contract.md"
    monkeypatch.setattr(work_contract, "record_path", lambda _sid: contract)
    contract.write_text(f"- 新着WIの判定: {names[0]} | 次回: 別ホストで観測する\n", encoding="utf-8")
    pending = gate("git push")
    assert pending is not None and names[0] not in pending and names[1] in pending
    _write_uwi_file(notes, names[1], answer="")
    assert gate("git push") is None
    _write_uwi_file(notes, names[0], answer="")
    assert gate("git push") is None
