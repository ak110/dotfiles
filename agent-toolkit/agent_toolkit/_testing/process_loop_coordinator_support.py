"""process_loopの試験で共有する準備処理と隔離fixture。"""

import json
import os
import pathlib

import pytest

from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing import wi_entry_files
from agent_toolkit._testing.process_loop_support import set_process_loop_repositories


def _targets(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, counts: tuple[int, ...] = (1, 1)
) -> tuple[pathlib.Path, tuple[listing.RepositorySnapshot, ...]]:
    notes = tmp_path / "notes"
    directory = notes / "2026/10"
    directory.mkdir(parents=True)
    paths = []
    for index, count in enumerate(counts):
        repo_id = f"example.test/team/repo-{index}"
        paths.append(git.init_repository(tmp_path / f"repo-{index}", origin=f"https://{repo_id}.git"))
        for number in range(count):
            (directory / f"repo-{index}-{number}.md").write_text(
                f"---\nstate: inbox\ntype: awi\ntarget_repo: {repo_id}\n---\n\n# 作業\n",
                encoding="utf-8",
            )
    set_process_loop_repositories(monkeypatch, paths)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setattr(
        session, "select_available_orchestrator", lambda *_args, **_kwargs: ("claude", "model", "medium", None, None)
    )
    monkeypatch.setattr(session, "resolve_orchestrator_specs", lambda: [("claude", "model", "medium")])
    return notes, listing.configured_repo_snapshot(notes)


def _manifest(prompt: str, environment: dict[str, str]) -> pathlib.Path:
    expected = (
        '<atk-auto source="process-loop" kind="goal">\n'
        "このセッションが統括として担当するagent-toolkit:process-wi all-reposの工程を完遂する"
        "（正当な先送りを結果へ記録した終了を含み、終了後に常駐が行う検収は含まない）\n</atk-auto>"
    )
    assert prompt.removeprefix("/goal ") == expected
    data = json.loads(environment["AGENT_TOOLKIT_SESSION_START_INPUT"])
    assert data["source"] == "process-loop" and data["mode"] == "all-repos"
    return pathlib.Path(data["manifest"])


def _start_entry(
    notes: pathlib.Path,
    name: str,
    *,
    state: str = "inbox",
    repo: str = "github.com/example/myrepo",
    answer: str | None = None,
    timestamp: float = 1000.0,
    kind: str = "uwi",
) -> pathlib.Path:
    path = wi_entry_files.entry_path(notes, name)
    body = "\n## 回答\n<!-- ユーザーはこの行以降に回答を追記する -->\n" + (answer or "") + "\n" if kind == "uwi" else ""
    path.write_text(f"---\nstate: {state}\ntype: {kind}\ntarget_repo: {repo}\n---\n\n質問\n{body}", encoding="utf-8")
    os.utime(path, (timestamp, timestamp))
    return path
