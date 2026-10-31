"""公開prepareから効果候補の期間・適用位置・未確認を検証する。"""

import json
import pathlib

import pytest
import session_review_prepare as prepare

from agent_toolkit._atk.wi import effect_check, uwi_scan
from agent_toolkit._common import state_paths
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.wi_entry_files import write_awi


def _tool(name: str, inputs: dict[str, str], number: int) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-10-31T12:00:10Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "id": f"call-{number}", "input": inputs}],
        },
    }


@pytest.mark.parametrize(
    "command,expected",
    [
        ("rg -n -e 基準 -- {path}", "candidate"),
        ("grep -n -e 基準 -- {path}", "candidate"),
        ("Get-Content -LiteralPath '{path}'", "candidate"),
        ("get-content -TotalCount 5 -Path '{path}'", "candidate"),
        ("awk 'NR <= 5' {path}", "candidate"),
        ("python {path}", "candidate"),
        ("rg --files -- {path}", "none"),
        ("rg -l -e 基準 -- {path}", "none"),
        ("grep --count -e 基準 -- {path}", "none"),
        ("rg -n -e '{path}' -- /unknown/output.txt", "unconfirmed"),
        ("cat $UNKNOWN", "unconfirmed"),
        ("Get-Content -UnknownOption '{path}'", "unconfirmed"),
    ],
)
def test_public_prepare_effect_shell_reads(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    expected: str,
) -> None:
    """公開prepareで本文読取・起動、列挙、検索パターン、判定不能な読取を区別する。"""
    plugin = "agent-" + "toolkit"
    relative = f"{plugin}/skills/example/SKILL.md"
    target = "/cache/vendor/agent-toolkit/1.2.3/skills/example/SKILL.md"
    worktree = git_repository.init_repository(
        tmp_path / "repo", origin="https://github.com/example/repo.git", files={relative: "規範"}, commit_message="規範変更"
    )
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    notes = tmp_path / "notes"
    monkeypatch.setattr(uwi_scan, "private_notes_root", lambda: notes)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    awi = write_awi(notes, "effect.md", state="adopted")
    awi.write_text(
        awi.read_text(encoding="utf-8") + f"\n## 処理結果\n\n- 処理日時: 2026-10-30T12:00:00Z\n- 対応commit: {oid}\n",
        encoding="utf-8",
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-10-31T12:00:00Z", "message": {"role": "user", "content": "依頼"}},
            _tool("Bash", {"command": command.format(path=target)}, 1),
        ],
    )
    output = tmp_path / "output"
    output.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--target-repo", str(worktree), "--work-dir", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    candidates = pathlib.Path(result["effect_candidates_path"]).read_text(encoding="utf-8").splitlines()
    unconfirmed = pathlib.Path(result["effect_unconfirmed_path"]).read_text(encoding="utf-8").splitlines()
    assert bool(candidates) == (expected == "candidate")
    assert bool(unconfirmed) == (expected == "unconfirmed")
    if candidates:
        assert json.loads(candidates[0])["file"] == relative
    if unconfirmed:
        assert all(json.loads(row)["reason"] for row in unconfirmed)


def test_public_prepare_effect_candidates_cover_period_and_application_sources(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """境界を含む14日、main・子・Skill・hook・rules、cache版、部分確認、対応不明のパスを同じ公開prepareで検証する。"""
    plugin = "agent-" + "toolkit"
    files = {
        f"{plugin}/skills/main/SKILL.md": "main",
        "agent-toolkit/share/child.md": "child",
        f"{plugin}/skills/invoked/SKILL.md": "skill",
        f"{plugin}/skills/root/SKILL.md": "root",
        f"{plugin}/skills/main/scripts/action.py": "pass",
        "agent-basics/skills/hook/SKILL.md": "hook",
        "agent-toolkit/rules/always.md": "rules",
        "agent-toolkit/share/unused.md": "unused",
    }
    worktree = git_repository.init_repository(
        tmp_path / "repo", origin="https://github.com/example/repo.git", files=files, commit_message="規範変更"
    )
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    notes = tmp_path / "notes"
    monkeypatch.setattr(uwi_scan, "private_notes_root", lambda: notes)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    for filename, date, commit, extra in (
        ("boundary.md", "2026-10-17T12:00:00Z", oid, ""),
        ("at-start.md", "2026-10-31T12:00:00Z", oid, ""),
        ("old.md", "2026-10-17T11:59:59Z", oid, ""),
        ("future.md", "2026-10-31T12:00:01Z", oid, ""),
        ("checked.md", "2026-10-20T12:00:00Z", oid, "effect_check: confirmed\n"),
        ("missing.md", "2026-10-20T12:00:00Z", None, ""),
        ("unresolved.md", "2026-10-20T12:00:00Z", "a" * 40, ""),
        ("partial.md", "2026-10-20T12:00:00Z", oid, ""),
    ):
        path = write_awi(notes, filename, state="adopted")
        text = path.read_text(encoding="utf-8").replace("state: adopted\n", "state: adopted\n" + extra)
        text += f"\n## 処理結果\n\n- 処理日時: {date}\n"
        if commit:
            text += f"- 対応commit: {commit}\n"
        if filename == "partial.md":
            text += (
                effect_check.RECORD_PREFIX
                + json.dumps({"file": f"{plugin}/skills/main/SKILL.md", "verdict": "confirmed"})
                + "\n"
            )
        path.write_text(text, encoding="utf-8")
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-10-31T12:00:00Z", "message": {"role": "user", "content": "依頼"}},
            _tool("Read", {"file_path": "/cache/vendor/agent-toolkit/1.2.3/skills/main/SKILL.md"}, 1),
            _tool("Skill", {"skill": f"{plugin}:invoked"}, 2),
            _tool("Read", {"file_path": "/unknown/document.md"}, 3),
            {"type": "user", "message": {"role": "user", "content": "agent-toolkit plugin root: /unusual_root\n次の指示"}},
            _tool("Read", {"file_path": "/unusual_root/skills/root/SKILL.md"}, 5),
            _tool("Bash", {"command": "uv run python /unusual_root/skills/main/scripts/action.py"}, 6),
            {
                "type": "attachment",
                "timestamp": "2026-10-31T12:00:20Z",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Read",
                    "toolUseID": "call-3",
                    "content": [
                        "未取得資料: " + "補足" * 200 + " `/cache/agent-basics/3.4.5/skills/hook/SKILL.md`を全文読め。"
                    ],
                },
            },
        ],
    )
    children = transcript.with_suffix("") / "subagents"
    children.mkdir(parents=True)
    (children / "agent-child.jsonl").write_text(
        json.dumps(_tool("Bash", {"command": "cat -- /cache/agent-toolkit/9.8.7/share/child.md"}, 4)) + "\n", encoding="utf-8"
    )
    output = tmp_path / "output"
    output.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--target-repo", str(worktree), "--work-dir", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    candidates = [
        json.loads(line) for line in pathlib.Path(result["effect_candidates_path"]).read_text(encoding="utf-8").splitlines()
    ]
    expected = set(files) - {"agent-toolkit/share/unused.md"}
    assert {row["file"] for row in candidates if row["awi"] == "boundary.md"} == expected
    assert {row["file"] for row in candidates if row["awi"] == "at-start.md"} == expected
    assert {row["file"] for row in candidates if row["awi"] == "partial.md"} == expected - {f"{plugin}/skills/main/SKILL.md"}
    assert {row["awi"] for row in candidates} == {"boundary.md", "at-start.md", "partial.md"}
    unresolved = [
        json.loads(line) for line in pathlib.Path(result["effect_unconfirmed_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert {row.get("awi") for row in unresolved if row.get("awi")} == {"missing.md", "unresolved.md"}
    assert any(row.get("path") == "/unknown/document.md" for row in unresolved)
    assert result["effect_candidates_count"] == len(candidates)
    assert result["effect_unconfirmed_count"] == len(unresolved)
    assert not result["unconfirmed_scope"]

    after = tmp_path / "after-output"
    after.mkdir()
    assert (
        prepare.main(
            [
                "--transcript",
                str(transcript),
                "--target-repo",
                str(worktree),
                "--work-dir",
                str(after),
                "--after",
                result["scope_path"],
            ]
        )
        == 0
    )
    incremental = json.loads(capsys.readouterr().out)
    assert (
        pathlib.Path(incremental["effect_candidates_path"]).read_bytes()
        == pathlib.Path(result["effect_candidates_path"]).read_bytes()
    )
