"""プロジェクトの作成・撤去hookの公開入力と返却先を検証する。"""

from __future__ import annotations

import http.server
import json
import os
import pathlib
import shlex
import subprocess
import sys
import threading

import pytest

from agent_toolkit._common import worktree_hooks
from agent_toolkit._testing import git_repository


def test_project_hook_lifecycle(tmp_path: pathlib.Path) -> None:
    """公式仕様の入力どおりにcommand hookへ対象のcwd、名前、所有sessionを渡し、作成時の返却先を撤去の入力へ引き継ぐ。"""
    # Claude Code hooks referenceのWorktreeCreate/Remove input/output（2026-10-13取得）。
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="基準")
    destination = tmp_path / "hook-worktree"
    captured = tmp_path / "captured.json"
    script = tmp_path / "hook.py"
    script.write_text(
        "import json,pathlib,subprocess,sys,os\n"
        "p=json.load(sys.stdin)\n"
        "assert p['cwd']==os.getcwd()==os.environ['CLAUDE_PROJECT_DIR']\n"
        "pathlib.Path(sys.argv[2]).write_text(json.dumps(p))\n"
        "if p['hook_event_name']=='WorktreeCreate':\n"
        " subprocess.run(['git','worktree','add','--detach',sys.argv[1]],check=True,stdout=sys.stderr)\n"
        " print(sys.argv[1])\n"
        "else:\n"
        " assert p['worktree_path']==sys.argv[1]\n"
        " subprocess.run(['git','worktree','remove',sys.argv[1]],check=True)\n",
        encoding="utf-8",
    )
    argv = [sys.executable, str(script), str(destination), str(captured)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    event: [{"hooks": [{"type": "command", "command": command}]}]
                    for event in ("WorktreeCreate", "WorktreeRemove")
                }
            }
        ),
        encoding="utf-8",
    )
    assert worktree_hooks.configured(repo)
    diagnostics = tmp_path / "diagnostics"
    created = worktree_hooks.create(repo, "fixed-name", "owner", diagnostics_directory=diagnostics)
    assert created == destination
    payload = json.loads(captured.read_text(encoding="utf-8"))
    assert payload["name"] == "fixed-name" and payload["session_id"] == "owner"
    worktree_hooks.remove(repo, created, "owner", diagnostics_directory=diagnostics)
    assert not destination.exists()


def test_hook_failure_preserves_diagnostic_and_partial_resource(tmp_path: pathlib.Path) -> None:
    """作成途中でhookが失敗してもGitによる作成へ切り替えず、作成済みの実体を残す。"""
    repo = tmp_path / "repo"
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir(parents=True)
    partial = tmp_path / "partial"
    argv = [sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).mkdir();raise SystemExit(3)", str(partial)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings.write_text(
        json.dumps({"hooks": {"WorktreeCreate": [{"hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8"
    )
    diagnostics = tmp_path / "diagnostics"
    with pytest.raises(ValueError, match="終了3") as failure:
        worktree_hooks.create(repo, "fixed-name", "owner", diagnostics_directory=diagnostics)
    assert partial.is_dir()
    record_path = diagnostics / "WorktreeCreate-0.json"
    assert str(record_path) in str(failure.value)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["exit_code"] == 3 and record["input"]["cwd"] == str(repo)
    assert record["input"]["name"] == "fixed-name" and record["input"]["session_id"] == "owner"


def test_http_project_hook_lifecycle(tmp_path: pathlib.Path) -> None:
    """HTTPの公式返却を作成先へ解決し、同じ返却先を撤去へ渡す。"""
    repo, destination = tmp_path / "repo", tmp_path / "created"
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir(parents=True)
    observed = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(payload)
            if payload["hook_event_name"] == "WorktreeCreate":
                destination.mkdir()
                body = {"hookSpecificOutput": {"hookEventName": "WorktreeCreate", "worktreePath": str(destination)}}
            else:
                pathlib.Path(payload["worktree_path"]).rmdir()
                body = {}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))

        def log_message(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

    with http.server.HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            hook = {"type": "http", "url": f"http://127.0.0.1:{server.server_port}/"}
            settings.write_text(
                json.dumps({"hooks": {event: [{"hooks": [hook]}] for event in ("WorktreeCreate", "WorktreeRemove")}}),
                encoding="utf-8",
            )
            diagnostics = tmp_path / "diagnostics"
            created = worktree_hooks.create(repo, "fixed", "owner", timeout=10, diagnostics_directory=diagnostics)
            assert created == destination
            worktree_hooks.remove(repo, created, "owner", timeout=10, diagnostics_directory=diagnostics)
            assert not destination.exists()
            assert observed[1]["worktree_path"] == str(destination)
        finally:
            server.shutdown()
            thread.join(timeout=10)
        assert not thread.is_alive()


@pytest.mark.parametrize("removes_on_failure", [False, True])
def test_remove_nonzero_uses_resource_presence_and_preserves_diagnostic(
    tmp_path: pathlib.Path, removes_on_failure: bool
) -> None:
    """作成先が無ければhookを呼ばず、hookの非0終了後は作成先の有無で成否を判定し、作成先が残った場合だけ次の撤去で同じhookを再び呼ぶ。"""
    repo = tmp_path / "repo"
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir(parents=True)
    destination, permit, calls = tmp_path / "created", tmp_path / "permit", tmp_path / "calls"
    script = tmp_path / "remove.py"
    script.write_text(
        "import json,pathlib,sys\n"
        "p=json.load(sys.stdin)\n"
        "pathlib.Path(sys.argv[1]).open('a').write(p['worktree_path']+'\\n')\n"
        "d=pathlib.Path(p['worktree_path'])\n"
        "if d.exists() and (sys.argv[3]=='remove' or pathlib.Path(sys.argv[2]).exists()): d.rmdir()\n"
        "raise SystemExit(0 if pathlib.Path(sys.argv[2]).exists() else 7)\n",
        encoding="utf-8",
    )
    argv = [sys.executable, str(script), str(calls), str(permit), "remove" if removes_on_failure else "keep"]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings.write_text(
        json.dumps({"hooks": {"WorktreeRemove": [{"hooks": [{"type": "command", "command": command}]}]}}),
        encoding="utf-8",
    )
    diagnostics = tmp_path / "diagnostics"
    worktree_hooks.remove(repo, destination, "owner", diagnostics_directory=diagnostics)
    assert not calls.exists() and not diagnostics.exists()
    destination.mkdir()
    if removes_on_failure:
        worktree_hooks.remove(repo, destination, "owner", diagnostics_directory=diagnostics)
    else:
        with pytest.raises(ValueError, match="終了7"):
            worktree_hooks.remove(repo, destination, "owner", diagnostics_directory=diagnostics)
    assert destination.exists() is (not removes_on_failure)
    failed = json.loads((diagnostics / "WorktreeRemove-0.json").read_text(encoding="utf-8"))
    assert failed["exit_code"] == 7 and failed["error"]
    permit.touch()
    worktree_hooks.remove(repo, destination, "owner", diagnostics_directory=tmp_path / "retry")
    assert not destination.exists()
    assert calls.read_text(encoding="utf-8").splitlines() == [str(destination)] * (1 if removes_on_failure else 2)
    assert (tmp_path / "retry").exists() is (not removes_on_failure)


@pytest.mark.parametrize(
    "response_body",
    [
        [],
        None,
        "text",
        {"hookSpecificOutput": []},
        {"hookSpecificOutput": {"hookEventName": "WorktreeCreate", "worktreePath": 1}},
    ],
)
def test_invalid_http_response_preserves_error_and_partial_resource(tmp_path: pathlib.Path, response_body: object) -> None:
    """HTTP応答の型不一致を形式失敗として保存し、作成済みの途中資源を保持する。"""
    repo, destination = tmp_path / "repo", tmp_path / "partial"
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir(parents=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            destination.mkdir()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(response_body).encode("utf-8"))

        def log_message(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

    with http.server.HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            hook = {"type": "http", "url": f"http://127.0.0.1:{server.server_port}/"}
            settings.write_text(json.dumps({"hooks": {"WorktreeCreate": [{"hooks": [hook]}]}}), encoding="utf-8")
            diagnostics = tmp_path / "diagnostics"
            with pytest.raises(ValueError, match="HTTP hook") as failure:
                worktree_hooks.create(repo, "fixed", "owner", timeout=10, diagnostics_directory=diagnostics)
            evidence = diagnostics / "WorktreeCreate-0.json"
            assert str(evidence) in str(failure.value) and destination.is_dir()
            observed = json.loads(evidence.read_text(encoding="utf-8"))
            assert observed["error"] and json.loads(observed["stdout"]) == response_body
        finally:
            server.shutdown()
            thread.join(timeout=10)
        assert not thread.is_alive()
