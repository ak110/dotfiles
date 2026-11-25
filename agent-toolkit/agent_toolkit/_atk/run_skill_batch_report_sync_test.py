"""報告未同期で失敗した振り返りbatchの診断を検証する。  pyfltr: allow-comment"""

# pylint: disable=protected-access
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import types

import filelock
import pytest

from agent_toolkit._atk import run_skill_batch, run_skill_batch_evidence, run_skill_batch_test
from agent_toolkit._atk.wi import add, frontmatter, layout, sync
from agent_toolkit._atk.wi.mutations import content
from agent_toolkit._common import session_activity, session_record_paths, session_review
from agent_toolkit._testing import git_repository

_STOP_SESSION = "36a3ac29-3ce6-48a9-85a7-e59e0bbcf0ab"


_SYNTHETIC_CHILD = """
import datetime
import os
import pathlib
import runpy
import sys

from agent_toolkit._atk.wi import add
from agent_toolkit._common import session_review

script, notes, target_repo, scenario = sys.argv[1:]
manifest = os.environ[session_review.BATCH_ENV]
if scenario == "unbound":
    os.environ.pop(session_review.BATCH_ENV)
add.add_entries(
    pathlib.Path(notes),
    messages=["---\\nrun_skill_report: true\\n---\\n# 振り返り結果\\n\\n確認した。"],
    target_repo=target_repo,
    source="run-skill",
    now=datetime.datetime.now(datetime.UTC),
    entry_type="uwi",
    scope=session_review.BATCH_SCOPE,
    question_type="post-approval",
)
os.environ[session_review.BATCH_ENV] = manifest
sys.argv = [script, "finish"]
runpy.run_path(script, run_name="__main__")
"""


@pytest.fixture(autouse=True)
def _isolate_session_inputs(monkeypatch):
    monkeypatch.delenv(session_review.LAUNCH_ENV, raising=False)
    monkeypatch.setattr(session_activity, "host_processes", lambda: ())


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.parametrize(
    ("saved", "reason"),
    [
        ("unbound", "報告用UWIが無い"),
        ("missing-run-id", None),
        ("uncommitted", None),
        ("unrelated-commit", None),
        ("push-rejected", None),
        ("push-rejected-then-allowed", None),
        ("synced", None),
    ],
)
def test_unsynced_report_failure_names_stage_and_stop_decisions(tmp_path, monkeypatch, saved, reason):
    """子が終了コード0で終わり、finishだけが失敗した事例を合成する。  pyfltr: allow-comment

    埋め込みの入力（準備済み、終了コード0、失敗イベントなし）を合成し、失敗本文から確認できなかった段階、
    近い候補のUWI、Stop hookの判定、配布元のcommitを読めることを確かめる。
    """
    notes = run_skill_batch_test._notes()
    origin = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.run_git(notes, "remote", "add", "origin", str(origin))
    git_repository.run_git(notes, "commit", "--allow-empty", "-m", "base")
    git_repository.run_git(notes, "push", "-u", "origin", "HEAD")
    for name, repo in (("one", "example.com/team/one"), ("two", "example.com/team/two"), ("three", None)):
        run_skill_batch_test._target(tmp_path, name, repo)
    manifests = []
    remote_before_recovery = []
    log = tmp_path / "model.log"
    log.write_text(
        'スキル: agent-toolkit:review-session\n[stdout] {"type":"assistant","message":"会話本文"}\n終了状態: exit code 0\n',
        encoding="utf-8",
    )
    stop_log = tmp_path / "stop.log"
    monkeypatch.setattr(session_record_paths, "stop_log_path", lambda _session_id: stop_log)
    stop_log.write_text(
        "2026-11-24T11:30:00 decision=block_run_skill_report_unsaved run_id=x scope=agent-toolkit:review-session batch\n"
        '2026-11-24T11:31:00 decision=approve_block_limit_reached details={"limit": 5}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        run_skill_batch_evidence.run_skill_state, "load", lambda _run_id: types.SimpleNamespace(session_id=_STOP_SESSION)
    )

    def execute(args):
        path = pathlib.Path(args.batch_manifest)
        manifests.append(path)
        session_review.write(path, {**session_review.read(path), "log_path": str(log)})
        script = run_skill_batch.PLUGIN_ROOT / "skills/atk-mode/scripts/session_review_state.py"
        child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", session_review.BATCH_ENV: str(path)}
        if saved in {"missing-run-id", "unbound"}:
            child_env.pop("AGENT_TOOLKIT_RUN_SKILL_ID", None)
        else:
            child_env["AGENT_TOOLKIT_RUN_SKILL_ID"] = args.run_id
        result = subprocess.run(
            [sys.executable, "-c", _SYNTHETIC_CHILD, str(script), str(notes), args.target_repo, saved],
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout.splitlines()[-1]) == {"run_id": args.run_id, "analysis_succeeded": True}
        if saved in {"uncommitted", "unrelated-commit", "push-rejected", "push-rejected-then-allowed", "missing-run-id"}:
            (notes / "unrelated.txt").write_text("報告と無関係。", encoding="utf-8")
            report = next(item for item in layout.iter_entry_paths(notes) if "source: run-skill" in item.read_text("utf-8"))
            old = report.read_text("utf-8")
            if saved == "missing-run-id":
                monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", "別の実行")
                updated = content._build_noninteractive_edit_content(report, old, "編集後の報告を確認したか？")
                parsed = frontmatter.parse_frontmatter(updated)
                assert parsed is not None and parsed[0]["run_skill_run_id"] == args.run_id
            else:
                report.write_text(old.replace("確認した。", "保存の後に書き換えた。"), encoding="utf-8")
            remote_before_recovery.append(git_repository.run_git(notes, "ls-remote", "origin", "refs/heads/*").stdout)
        if saved == "unrelated-commit":
            git_repository.run_git(notes, "add", "unrelated.txt")
            git_repository.run_git(notes, "commit", "-m", "無関係な保留commit")
        if saved.startswith("push-rejected"):
            hook = origin / "hooks" / "pre-receive"
            hook.write_text("#!/bin/sh\necho 'fixture rejection token=secret-to-hide' >&2\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
        return result.returncode

    args = run_skill_batch_test._args("manual")
    assert run_skill_batch.dispatch(args, execute) == (1 if reason else 0)
    assert len(manifests) == 1
    record = session_review.read(manifests[0])
    assert len(record["prepared"]) == 3
    if reason is None:
        assert record["status"] == "completed"
        assert session_review.pending() == []
        assert not run_skill_batch_test._failure_awis(notes)
        if saved == "uncommitted":
            assert "?? unrelated.txt" in git_repository.run_git(notes, "status", "--short").stdout
        if saved.startswith("push-rejected"):
            assert git_repository.run_git(notes, "ls-remote", "origin", "refs/heads/*").stdout == remote_before_recovery[0]
        assert add.has_committed_run_report(notes, run_id=record["run_id"], scope=session_review.BATCH_SCOPE)
        return
    body = run_skill_batch_test._failure_awis(notes)[0].read_text(encoding="utf-8")
    details = json.loads("\n".join(line[4:] for line in body.splitlines() if line.startswith("    ")))
    assert details["error"] == f"finish: report-unsynced: 報告の同期を確認できない（{reason}）"
    report_sync = details["failure_diagnostics"]["report_sync"]
    assert report_sync["reason"] == reason
    if saved == "unbound":
        assert report_sync["candidate_count"] == 1
        assert report_sync["candidates"][0]["run_skill_run_id"] is None
        assert report_sync["candidates"][0]["scope"] == session_review.BATCH_SCOPE
    assert details["stop_log"]["status"] == "取得済み" and details["stop_log"]["session_id"] == _STOP_SESSION
    assert "decision=approve_block_limit_reached" in details["stop_log"]["tail"][-1]
    assert details["plugin_source"]["commit"]
    assert details["exit_code"] is None and details["log"]["tail"] == "終了状態: exit code 0"
    assert "初期要求" not in body and "会話本文" not in body


@pytest.mark.usefixtures("leaf_git_commands")
def test_report_lock_contention_keeps_analysis_and_resumes_without_child(tmp_path, monkeypatch, capsys):
    notes = run_skill_batch_test._notes()
    run_skill_batch_test._target(tmp_path, "one", "example.com/team/one")
    calls: list[str] = []
    execute = run_skill_batch_test._report_and_finish(notes, monkeypatch, calls)
    lock_path = sync.repo_lock_path(notes)
    real_lock = sync.repo_lock
    manifests = []

    with filelock.FileLock(lock_path, timeout=0) as held:
        held.release()

        def execute_then_lock(args):
            manifests.append(pathlib.Path(args.batch_manifest))
            result = execute(args)
            held.acquire()
            monkeypatch.setattr(sync, "repo_lock", lambda repo, timeout=None: real_lock(repo, timeout=0))
            return result

        try:
            assert run_skill_batch.dispatch(run_skill_batch_test._args("manual"), execute_then_lock) == 1
        finally:
            held.release()

    assert len(manifests) == 1
    manifest = manifests[0]
    unfinished = session_review.read(manifest)
    assert unfinished["analysis_succeeded"] and unfinished["status"] == "running"
    assert "確定を保留" in capsys.readouterr().err
    assert not run_skill_batch_test._failure_awis(notes)
    args = run_skill_batch_test._args("manual")
    args.batch_manifest, args.run_id, args.resume_record = str(manifest), unfinished["run_id"], object()

    assert run_skill_batch.dispatch(args, lambda _: pytest.fail("分析済みの子を再実行した")) == 0
    assert session_review.read(manifest)["status"] == "completed"
    assert len(calls) == 1 and not session_review.pending()


@pytest.mark.parametrize(
    ("install_path", "version", "expected"),
    [
        ("root", "9.9.9", ("c" * 40, "導入先が一致")),
        ("other", "9.9.9", ("c" * 40, "版数が一致")),
        ("other", "0.0.1", (None, "特定できない")),
    ],
)
def test_plugin_source_reads_claude_install_record(tmp_path, monkeypatch, install_path, version, expected):
    """Gitで管理されない導入先では、Claude Codeの導入記録から配布元のcommitを取る。  pyfltr: allow-comment"""
    root = tmp_path / "root"
    (root / ".claude-plugin").mkdir(parents=True)
    config = tmp_path / "claude"
    (config / "plugins").mkdir(parents=True)
    entry = {"installPath": str(tmp_path / install_path), "version": "9.9.9", "gitCommitSha": "c" * 40}
    (config / "plugins/installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"agent-toolkit@market": [entry]}}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    source = run_skill_batch_evidence.plugin_source(root, version)
    assert source["commit"] == expected[0] and expected[1] in source["source"]
