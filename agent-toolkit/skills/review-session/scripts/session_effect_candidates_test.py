"""公開prepareから効果候補の期間・適用位置・未確認を検証する。"""

import datetime
import json
import pathlib

import pytest
import session_review_prepare as prepare

from agent_toolkit import atk
from agent_toolkit._atk.wi import effect_check, frontmatter, readiness, sync, uwi_scan
from agent_toolkit._atk.wi.mutations import targets
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
        "agent-toolkit/share/rules/common/always.md": "rules",
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
        ("naive.md", "2026-10-20T12:00:00", oid, ""),
        ("bad-date.md", "不明", oid, "effect_check: pending\n"),
        ("no-date.md", None, oid, "effect_check: pending\n"),
    ):
        path = write_awi(notes, filename, state="adopted")
        text = path.read_text(encoding="utf-8").replace("state: adopted\n", "state: adopted\n" + extra)
        text += "\n## 処理結果\n\n"
        if date is not None:
            text += f"- 処理日時: {date}\n"
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
    assert {row["awi"] for row in candidates} == {"boundary.md", "at-start.md", "partial.md", "naive.md"}
    assert {row["file"] for row in candidates if row["awi"] == "naive.md"} == expected
    assert all(
        row["assumed_timezone"] and row["adopted_at"] == "2026-10-20T12:00:00" for row in candidates if row["awi"] == "naive.md"
    )
    unresolved = [
        json.loads(line) for line in pathlib.Path(result["effect_unconfirmed_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert {row.get("awi") for row in unresolved if row.get("awi")} == {
        "missing.md",
        "unresolved.md",
        "bad-date.md",
        "no-date.md",
    }
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


def test_public_adopt_to_prepare_and_effect_lifecycle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """実採用データを後日のprepareへ渡し、未確認・部分確認・保存再開・全体確認を接続する。"""
    files = {"app.py": "print('結果')\n", "agent-toolkit/skills/review-session/SKILL.md": "適用する規範\n"}
    worktree = git_repository.init_repository(
        tmp_path / "repo", origin="https://github.com/example/repo.git", files=files, commit_message="仕組みを実装する"
    )
    oid = git_repository.git_output(worktree, "rev-parse", "HEAD")
    notes = tmp_path / "notes"
    awi = write_awi(notes, "effect.md", state="processing")
    awi.write_text(awi.read_text(encoding="utf-8") + "\n## ユーザーコメント\n\n保持する要求\n", encoding="utf-8")
    write_awi(notes, "dependent.md", depends_on=(awi.name,))
    write_awi(notes, "corrective.md")
    unnecessary = write_awi(notes, "unnecessary.md", state="processing")
    insufficient = write_awi(notes, "insufficient.md", state="processing")
    remote = git_repository.init_bare_repository(tmp_path / "notes.git")
    git_repository.init_repository(notes, initial_branch="main", origin=str(remote), commit_message="処理対象")
    git_repository.run_git(notes, "push", "--set-upstream", "origin", "main")
    monkeypatch.setattr(sync, "ensure_environment", lambda *_args, **_kwargs: notes)
    monkeypatch.setattr(uwi_scan, "private_notes_root", lambda: notes)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")

    with pytest.raises(SystemExit) as adopted:
        atk.main(
            ["wi", "adopt", awi.name, unnecessary.name, insufficient.name, "--target-repo", str(worktree), "--commit", oid],
            home=tmp_path,
            now=datetime.datetime(2026, 10, 1, 12),
        )
    assert adopted.value.code == 0
    capsys.readouterr()
    saved_adoption = awi.read_text(encoding="utf-8")
    dates = effect_check.result_values(saved_adoption, "処理日時")
    assert readiness.calculate_readiness(notes, "github.com/example/repo").ready == ("corrective.md", "dependent.md")

    def effect(verdict: str, *extra: str, filename: str = awi.name) -> int:
        with pytest.raises(SystemExit) as finished:
            atk.main(
                [
                    "wi",
                    "effect-check",
                    filename,
                    "--worktree",
                    str(worktree),
                    "--verdict",
                    verdict,
                    "--checked-on",
                    "2026-10-31",
                    "--review-record",
                    "session:42",
                    *extra,
                ],
                home=tmp_path,
            )
        capsys.readouterr()
        assert isinstance(finished.value.code, int)
        return finished.value.code

    def candidates(label: str, *, used: bool, started: str = "2026-10-31T12:00:00Z") -> tuple[list[dict], list[dict]]:
        rows = [{"type": "user", "timestamp": started, "message": {"role": "user", "content": "作業依頼"}}]
        if used:
            for index, file in enumerate(files, 1):
                event = _tool("Read", {"file_path": str(worktree / file)}, index)
                event["timestamp"] = (datetime.datetime.fromisoformat(started) + datetime.timedelta(seconds=index)).isoformat()
                rows.append(event)
        transcript_dir = tmp_path / label
        transcript_dir.mkdir()
        transcript = _write_transcript(transcript_dir, rows)
        output = transcript_dir / "output"
        output.mkdir()
        assert prepare.main(["--transcript", str(transcript), "--target-repo", str(worktree), "--work-dir", str(output)]) == 0
        result = json.loads(capsys.readouterr().out)
        selected, unconfirmed = (
            [json.loads(line) for line in pathlib.Path(result[key]).read_text(encoding="utf-8").splitlines()]
            for key in ("effect_candidates_path", "effect_unconfirmed_path")
        )
        return selected, unconfirmed

    # 採用側のnaive値だけを消費側がUTCとみなす不一致を、採用直後の候補化で検出する。
    immediate_start = (
        datetime.datetime.fromisoformat(dates[0]).astimezone(datetime.UTC) + datetime.timedelta(minutes=1)
    ).isoformat()
    immediate, _ = candidates("immediate", used=True, started=immediate_start)
    assert {row["file"] for row in immediate if row["awi"] == awi.name} == set(files)
    assert awi.read_text(encoding="utf-8") == saved_adoption
    assert datetime.datetime.fromisoformat(dates[0]).tzinfo is not None
    assert "effect_check: pending" in saved_adoption
    initial, waiting = candidates("unused", used=False)
    assert not initial
    assert {row["file"] for row in waiting if row.get("awi") == awi.name} == set(files)
    assert awi.read_text(encoding="utf-8") == saved_adoption
    assert effect("pending", "--reason", "適用機会なし") == 0
    assert effect("pending", "--reason", "適用機会なし") == 0
    assert len(effect_check.result_values(awi.read_text(encoding="utf-8"), "効果確認全体")) == 1
    later, _ = candidates("used", used=True)
    assert {row["file"] for row in later if row["awi"] == awi.name} == set(files)
    assert all(row["adopted_at"] == dates[0] and row["uses"] for row in later)

    assert effect("confirmed", "--file", "app.py") == 0
    partial = awi.read_text(encoding="utf-8")
    assert "effect_check: pending" in partial
    remaining, _ = candidates("partial", used=True)
    assert {row["file"] for row in remaining if row["awi"] == awi.name} == {"agent-toolkit/skills/review-session/SKILL.md"}

    def fail_write(_path: pathlib.Path, _text: str, **_kwargs: object) -> None:
        raise OSError("試験用の保存失敗")

    with monkeypatch.context() as failing:
        failing.setattr(targets, "atomic_write_text", fail_write)
        assert effect("confirmed", "--file", "agent-toolkit/skills/review-session/SKILL.md") != 0
    assert awi.read_text(encoding="utf-8") == partial
    assert effect("confirmed", "--file", "agent-toolkit/skills/review-session/SKILL.md") == 0
    assert effect("confirmed", "--file", "agent-toolkit/skills/review-session/SKILL.md") == 0
    complete = awi.read_text(encoding="utf-8")
    assert "effect_check: confirmed" in complete
    assert len(effect_check.result_values(complete, "効果確認")) == 2
    assert effect_check.result_values(complete, "処理日時") == dates
    assert "## ユーザーコメント\n\n保持する要求\n" in complete
    parsed = frontmatter.parse_frontmatter(complete)
    assert parsed is not None and parsed[0]["state"] == "adopted"
    assert effect("not_required", "--reason", "後日の確認を要しない変更", filename=unnecessary.name) == 0
    assert effect("insufficient", "--file", "app.py", "--corrective-awi", "corrective.md", filename=insufficient.name) == 0
    for path, expected in ((unnecessary, "not_required"), (insufficient, "insufficient")):
        text = path.read_text(encoding="utf-8")
        assert f"effect_check: {expected}" in text
        assert effect_check.result_values(text, "処理日時") == dates
    terminal, _ = candidates("complete", used=True)
    assert not terminal
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == git_repository.git_output(notes, "rev-parse", "origin/main")
