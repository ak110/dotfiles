"""隔離private-notesと公開CLIで固定配置の全遷移・移行・履歴を検証する。"""

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from agent_toolkit._atk.wi import constants, entries
from agent_toolkit._atk.wi import process_loop_launch_gate as launch_gate
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY

_REPO = "github.com/example/repo"


def _run(notes: pathlib.Path, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "agent_toolkit.atk", "wi", *arguments],
        cwd=notes.parent,
        env={**os.environ, "AGENT_TOOLKIT_PRIVATE_NOTES": str(notes)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    return result


def test_public_transitions_keep_one_dated_file(tmp_path: pathlib.Path) -> None:
    """公開addから全指定状態へ遷移し、filenameと実体位置を保持する。"""
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="初期状態")
    body = tmp_path / "body.md"
    body.write_text(AGENT_AWI_BODY, encoding="utf-8")
    _run(notes, "add", "--source=human", f"--target-repo={_REPO}", "--body-file", str(body))
    paths = list(entries.iter_entry_paths(notes))
    assert len(paths) == 1
    path = paths[0]
    filename = path.name
    assert path == notes / filename[:4] / filename[4:6] / filename
    for action, state, flags in (
        ("start-processing", "processing", []),
        ("hold", "hold", ["--state=processing"]),
        ("unhold", "inbox", []),
        ("adopt", "adopted", ["--note=採用"]),
        ("return-to-inbox", "inbox", ["--state=adopted"]),
        ("reject", "rejected", ["--note=不採用"]),
        ("return-to-inbox", "inbox", ["--state=rejected"]),
    ):
        _run(notes, action, filename, *flags, f"--target-repo={_REPO}")
        assert list(entries.iter_entry_paths(notes)) == [path]
        assert entries.entry_state_of(path) == state
        assert not any((notes / previous).exists() for previous in constants.WI_STATES)


def _selected(notes: pathlib.Path, state: str) -> set[str]:
    return {
        record[0].name
        for record in entries.select_entries(
            notes, status=state, target_repo=_REPO, entry_type="all", answered="all", source=None
        )
    }


def test_public_startup_migrates_all_states_and_keeps_history_and_selection(tmp_path: pathlib.Path) -> None:
    """一度の起動で全状態と旧種別を移し、履歴・状態別read集合・loop候補・遅着項目を維持する。"""
    names = {state: f"20261020-120000-{number:03d}.md" for number, state in enumerate(constants.WI_STATES, 1)}
    files = {
        f"{state}/{name}": f"---\ntype: awi\ntarget_repo: {_REPO}\n---\n\n# 移行対象\n" + "保存する本文\n" * 20
        for state, name in names.items()
    }
    old_question = "20261020-120000-006.md"
    old_feedback = "20261020-120000-007.md"
    files[f"tbd/inbox/{old_question}"] = f"---\ntarget_repo: {_REPO}\n---\n\n## 質問\n\n移行対象\n\n## 回答\n\n"
    files[f"feedback/hold/{old_feedback}"] = f"---\ntarget_repo: {_REPO}\n---\n\n# 移行対象\n" + "保存する本文\n" * 20
    notes = git_repository.init_repository(tmp_path / "notes", files=files, commit_message="旧配置の全状態")
    original_head = git_repository.git_output(notes, "rev-parse", "HEAD")
    # 旧種別は旧readerの対象外なので、現行state配置だけの移行前集合を先に控える。
    before = {state: _selected(notes, state) for state in constants.WI_STATES}
    loop_before = launch_gate.target_input(notes, _REPO, None)
    _run(notes, "list", "--state=all", "--jsonl", "--skip-pull", f"--target-repo={_REPO}")
    assert git_repository.git_output(notes, "rev-list", "--count", "HEAD") == "2"
    assert not any((notes / state).exists() for state in (*constants.WI_STATES, "tbd", "feedback"))
    for state, name in [*names.items(), ("inbox", old_question), ("hold", old_feedback)]:
        path = notes / "2026" / "10" / name
        assert entries.entry_state_of(path) == state
        history = git_repository.git_output(notes, "log", "--follow", "--format=%H", "--", str(path.relative_to(notes)))
        assert original_head in history.splitlines()
    loop_after = launch_gate.target_input(notes, _REPO, None)
    assert {(name, kind) for name, _digest, kind in loop_after.items} == {
        (name, kind) for name, _digest, kind in loop_before.items
    }
    for state in constants.WI_STATES:
        expected = before[state] | {"inbox": {old_question}, "hold": {old_feedback}}.get(state, set())
        common = [f"--state={state}", "--skip-pull", f"--target-repo={_REPO}"]
        listed = _run(notes, "list", "--jsonl", *common)
        assert {json.loads(line)["filename"] for line in listed.stdout.splitlines()} == expected
        shown = _run(notes, "show", "--all", "--summary-only", *common)
        assert set(re.findall(r"^### (\S+\.md) \[", shown.stdout, re.MULTILINE)) == expected
        found = _run(notes, "grep", "-l", "-F", "移行対象", *common)
        assert set(found.stdout.splitlines()) == expected
    late = notes / "inbox" / "20261020-120000-008.md"
    late.parent.mkdir()
    late.write_text(f"---\ntype: awi\ntarget_repo: {_REPO}\n---\n\n# 旧版の遅着\n", encoding="utf-8")
    _run(notes, "list", "--state=all", "--jsonl", "--skip-pull", f"--target-repo={_REPO}")
    assert not late.exists()
    assert (notes / "2026" / "10" / late.name).exists()


@pytest.mark.parametrize("problem", ["collision", "frontmatter"])
def test_public_migration_reports_every_problem_without_partial_changes(tmp_path: pathlib.Path, problem: str) -> None:
    """全衝突と全破損を一度に診断し、正常候補も含む全本文とHEADを変更しない。"""
    names = [f"20261020-120000-{number:03d}.md" for number in range(1, 4)]
    content = f"---\ntype: awi\ntarget_repo: {_REPO}\n---\n\n# 要求\n"
    files = {f"inbox/{name}": content for name in names}
    for name in names[1:]:
        if problem == "collision":
            files[f"2026/10/{name}"] = content.replace("type: awi", "type: awi\nstate: hold")
        else:
            files[f"inbox/{name}"] = "---\ninvalid: [\n---\n\n破損\n"
    notes = git_repository.init_repository(tmp_path / "notes", files=files, commit_message="問題を含む旧配置")
    before = {path.relative_to(notes): path.read_bytes() for path in notes.rglob("*.md")}
    head = git_repository.git_output(notes, "rev-parse", "HEAD")
    result = _run(notes, "list", "--state=all", "--skip-pull", f"--target-repo={_REPO}", expected=2)
    assert all(name in result.stderr for name in names[1:])
    assert {path.relative_to(notes): path.read_bytes() for path in notes.rglob("*.md")} == before
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == head
