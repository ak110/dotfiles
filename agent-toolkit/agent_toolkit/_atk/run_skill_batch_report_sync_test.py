"""報告未同期で失敗した振り返りbatchの診断を検証する。  pyfltr: allow-comment"""

# pylint: disable=protected-access
from __future__ import annotations

import datetime
import json
import pathlib
import types

import pytest

from agent_toolkit._atk import run_skill_batch, run_skill_batch_evidence, run_skill_batch_test
from agent_toolkit._atk.wi import add, layout
from agent_toolkit._common import session_activity, session_record_paths, session_review
from agent_toolkit._testing import git_repository

_STOP_SESSION = "36a3ac29-3ce6-48a9-85a7-e59e0bbcf0ab"


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
        if saved == "missing-run-id":
            monkeypatch.setenv(session_review.BATCH_ENV, str(path))
        if saved in {"missing-run-id", "unbound"}:
            # 実行識別子を受け取らない経路で保存した報告は、この実行の報告として数えない。  # pyfltr: allow-comment
            monkeypatch.delenv("AGENT_TOOLKIT_RUN_SKILL_ID", raising=False)
        else:
            monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", args.run_id)
        add.add_entries(
            notes,
            messages=["---\nrun_skill_report: true\n---\n# 振り返り結果\n\n確認した。"],
            target_repo=args.target_repo,
            source="run-skill",
            now=datetime.datetime.now(datetime.UTC),
            entry_type="uwi",
            scope=session_review.BATCH_SCOPE,
            question_type="post-approval",
        )
        if saved == "uncommitted":
            (notes / "unrelated.txt").write_text("報告と無関係。", encoding="utf-8")
            report = next(item for item in layout.iter_entry_paths(notes) if "source: run-skill" in item.read_text("utf-8"))
            report.write_text(report.read_text("utf-8").replace("確認した。", "保存の後に書き換えた。"), encoding="utf-8")
        session_review.BatchWriter(path).analyze()
        return 0

    args = run_skill_batch_test._args("manual")
    assert run_skill_batch.dispatch(args, execute) == (1 if reason else 0)
    assert len(manifests) == 1
    record = session_review.read(manifests[0])
    assert len(record["prepared"]) == 3
    if reason is None:
        assert record["status"] == "completed"
        assert not run_skill_batch_test._failure_awis(notes)
        if saved == "uncommitted":
            assert "?? unrelated.txt" in git_repository.run_git(notes, "status", "--short").stdout
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
