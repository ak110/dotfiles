"""非同期振り返りのhookからrun-skill・状態・報告へ渡る契約を隔離して検証する。"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import sysconfig
import types

import filelock
import pytest

from agent_toolkit._atk import config, run_script, run_skill_batch
from agent_toolkit._atk.wi import add, frontmatter, layout
from agent_toolkit._common import session_activity, session_review
from agent_toolkit._testing import git_repository


def _target(tmp_path, name, target_repo: str | None = "https://example.com/team/repo"):
    transcript = tmp_path / f"{name}.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "timestamp": "2026-10-28T00:00:00Z", "message": {"role": "user", "content": "初期要求"}})
        + "\n",
        encoding="utf-8",
    )
    session_review.record(
        {"hook_event_name": "SessionStart", "session_id": name, "transcript_path": str(transcript), "cwd": str(tmp_path)},
        host="claude",
        target_repo=target_repo,
    )


def _args(mode="auto"):
    return argparse.Namespace(skill=session_review.BATCH_SKILL, args=mode, timeout=60, target_repo=None, work_dir=None)


def _notes():
    path = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    git_repository.init_repository(path)
    return path


def test_hook_disabled_records_and_announces_pending(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")
    notice = run_skill_batch.hook_request(
        {"hook_event_name": "SessionStart", "session_id": "main", "cwd": str(tmp_path)}, host="codex"
    )
    assert notice is not None
    assert "1件" in notice and "/agent-toolkit:session-review-batch" in notice
    assert session_review.pending()[0]["host"] == "codex"
    assert run_skill_batch.hook_request({"hook_event_name": "SessionEnd", "session_id": "main"}, host="codex") is None


def test_hook_requests_existing_cli_without_wait(tmp_path, monkeypatch) -> None:
    calls = []

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return types.SimpleNamespace(pid=None, wait=lambda: 0)

    monkeypatch.setattr(run_skill_batch.repo, "resolve_repo_id_or_raise", lambda *args, **kwargs: "example.com/team/repo")
    monkeypatch.setattr(session_activity, "host_process", object)
    monkeypatch.setattr(run_skill_batch.subprocess, "Popen", spawn)
    run_skill_batch.hook_request({"hook_event_name": "SessionStart", "session_id": "main", "cwd": str(tmp_path)}, host="claude")
    assert len(calls) == 1
    assert calls[0][0][-4:] == ["run-skill", "--args", "auto", session_review.BATCH_SKILL]
    assert calls[0][1]["stdin"] == run_skill_batch.subprocess.DEVNULL


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
@pytest.mark.parametrize("host", ["claude", "codex"])
def test_hook_cli_resolves_package_without_editable_install(tmp_path, monkeypatch, event, host) -> None:
    """hookのargvと環境で実CLIを起動し、editable導入の無い配布環境でも引数解析へ届く。"""
    # -Sでeditableの.pthだけを読ませず、依存パッケージは同じ環境から利用する。
    monkeypatch.setenv("PYTHONPATH", sysconfig.get_path("purelib"))
    _target(tmp_path, "main")
    monkeypatch.setattr(run_skill_batch.repo, "resolve_repo_id_or_raise", lambda *args, **kwargs: "example.com/team/repo")
    monkeypatch.setattr(session_activity, "host_process", object)
    original_popen = subprocess.Popen
    results = []

    def spawn(argv, **kwargs):
        with original_popen(
            [argv[0], "-S", *argv[1:], "--help"],
            cwd=kwargs["cwd"],
            env=kwargs["env"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as child:
            stdout, stderr = child.communicate(timeout=30)
            results.append((child.returncode, stdout, stderr))
        return types.SimpleNamespace(pid=None, wait=lambda: 0)

    monkeypatch.setattr(run_skill_batch.subprocess, "Popen", spawn)
    run_skill_batch.hook_request({"hook_event_name": event, "session_id": "main", "cwd": str(tmp_path)}, host=host)
    assert len(results) == 1
    code, stdout, stderr = results[0]
    assert code == 0, stderr.decode("utf-8", errors="replace")
    assert "atk run-skill" in stdout.decode("utf-8")


@pytest.mark.parametrize("condition", ["empty", "active", "locked", "running"])
def test_hook_launch_normal_outcomes_do_not_report_failure(tmp_path, monkeypatch, condition):
    """対象なし・活動中のみ・排他・実行中を受付前失敗から区別する。"""
    launch_id = "a" * 32
    path = session_review.directory() / "launches" / f"{launch_id}.json"
    session_review.write(
        path, {"status": "starting", "exited": True, "host": "codex", "event": "SessionEnd", "log_path": "log"}
    )
    monkeypatch.setenv(session_review.LAUNCH_ENV, launch_id)

    def unexpected(args):
        raise AssertionError("モデルは起動しない")

    if condition == "active":
        _target(tmp_path, "active")
        monkeypatch.setattr(
            session_activity, "review_activity", lambda *args, **kwargs: types.SimpleNamespace(has_activity=True)
        )
    if condition == "running":
        identity = run_skill_batch.session_registry.ProcessIdentity.of(os.getpid())
        assert identity is not None
        value = session_review.read(path)
        value["process"] = {"pid": identity.pid, "create_time": identity.create_time}
        session_review.write(path, value)
    elif condition == "locked":
        with filelock.FileLock(str(session_review.directory() / "batch.lock")):
            assert run_skill_batch.dispatch(_args(), unexpected) == 1
    else:
        assert run_skill_batch.dispatch(_args(), unexpected) == 0
    assert session_review.launch_notice() is None
    assert bool(session_review.pending()) == (condition == "active")


def test_batch_prepares_all_and_commits_scope_after_one_report(tmp_path, monkeypatch) -> None:
    notes = _notes()
    _target(tmp_path, "one")
    _target(tmp_path, "two")
    calls = []

    def execute(args):
        manifest = session_review.read(pathlib.Path(args.batch_manifest))
        calls.append(manifest)
        assert len(manifest["prepared"]) == 2
        assert len(session_review.pending()) == 2
        monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", args.run_id)
        monkeypatch.setenv(session_review.BATCH_ENV, args.batch_manifest)
        add.add_entries(
            notes,
            messages=["---\nrun_skill_report: true\n---\n# 振り返り結果\n\n2件を確認した。"],
            target_repo=args.target_repo,
            source="run-skill",
            now=datetime.datetime.now(datetime.UTC),
            entry_type="uwi",
            scope=session_review.BATCH_SKILL,
            question_type="post-approval",
        )
        assert run_script.dispatch(argparse.Namespace(script_name="session-review-state", script_args=["finish"])) == 0
        return 0

    assert run_skill_batch.dispatch(_args(), execute) == 0
    assert len(calls) == 1 and not session_review.pending()
    assert len(list(layout.iter_entry_paths(notes))) == 1


def test_failure_blocks_auto_but_manual_can_retry_without_duplicate_report(tmp_path) -> None:
    notes = _notes()
    _target(tmp_path, "one")
    calls = []

    def fail(args):
        calls.append(args)
        return 124

    assert run_skill_batch.dispatch(_args(), fail) == 124
    assert run_skill_batch.dispatch(_args(), fail) == 0
    assert len(calls) == 1
    assert run_skill_batch.dispatch(_args("manual"), fail) == 124
    assert len(calls) == 2 and len(list(layout.iter_entry_paths(notes))) == 1
    assert session_review.pending()


def test_global_lock_prevents_different_repo_execution(tmp_path) -> None:
    _target(tmp_path, "one")

    def unexpected(args):
        raise AssertionError("モデルは起動しない")

    with filelock.FileLock(str(session_review.directory() / "batch.lock")):
        assert run_skill_batch.dispatch(_args(), unexpected) == 1


def test_missing_finish_keeps_report_and_suppresses_auto_retry(tmp_path, monkeypatch) -> None:
    """報告だけ保存した中断でも報告は残し、配布元への是正AWIが未完了の間は再起動を抑える。"""
    notes = _notes()
    _target(tmp_path, "one")
    calls = []

    def execute(args):
        calls.append(args)
        monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", args.run_id)
        add.add_entries(
            notes,
            messages=["---\nrun_skill_report: true\n---\n# 振り返り結果\n\n報告まで保存した。"],
            target_repo=args.target_repo,
            source="run-skill",
            now=datetime.datetime.now(datetime.UTC),
            entry_type="uwi",
            scope=session_review.BATCH_SKILL,
            question_type="post-approval",
        )
        return 0

    assert run_skill_batch.dispatch(_args(), execute) == 1
    assert run_skill_batch.dispatch(_args(), execute) == 0
    assert len(calls) == 1
    assert len(list(layout.iter_entry_paths(notes))) == 2
    failures = _failure_awis(notes)
    assert len(failures) == 1
    assert "analysis-incomplete" in failures[0].read_text(encoding="utf-8")


def test_active_target_is_not_prepared(tmp_path, monkeypatch) -> None:
    _target(tmp_path, "one")

    def active(session_ids, **kwargs):
        del kwargs
        return session_activity.ActivitySnapshot(frozenset(session_ids), ("活動中",))

    monkeypatch.setattr(session_activity, "review_activity", active)

    def unexpected(args):
        raise AssertionError("モデルは起動しない")

    assert run_skill_batch.dispatch(_args(), unexpected) == 0
    assert len(session_review.pending()) == 1


def test_config_boolean_contract() -> None:
    assert config.resolve_mutable_setting("session_review_auto_start") == "true"
    config.save_mutable_settings({"session_review_auto_start": "false"})
    assert config.resolve_mutable_setting("session_review_auto_start") == "false"
    with pytest.raises(ValueError, match="true, false"):
        config.save_mutable_settings({"session_review_auto_start": "yes"})


def test_failure_awi_close_releases_failed_target(tmp_path) -> None:
    """是正AWIが未完了の間は同じ対象を起動せず、AWIが終了すると次の自動起動で対象へ戻す。"""
    notes = _notes()
    _target(tmp_path, "one")
    calls = []

    def fail(args):
        calls.append(args)
        return 124

    assert run_skill_batch.dispatch(_args(), fail) == 124
    assert run_skill_batch.dispatch(_args(), fail) == 0
    assert len(calls) == 1
    report = next(layout.iter_entry_paths(notes))
    text = report.read_text(encoding="utf-8")
    report.write_text(text.replace("state: inbox\n", "state: adopted\n", 1), encoding="utf-8")
    assert run_skill_batch.dispatch(_args(), fail) == 124
    assert len(calls) == 2
    assert len(list(layout.iter_entry_paths(notes))) == 2


def test_batch_uses_most_common_repo_and_unknown_repo_fallback(tmp_path) -> None:
    """実際に選んだ対象数で報告先を決め、全対象不明なら配布元URLと独立cwdを使う。"""
    notes = _notes()
    for name in ("one", "two"):
        _target(tmp_path, name, "https://example.com/team/most")
    _target(tmp_path, "three", "https://example.com/team/other")
    observed = []

    def fail(args):
        observed.append(args.target_repo)
        return 124

    assert run_skill_batch.dispatch(_args(), fail) == 124
    assert observed == ["https://example.com/team/most"]
    for path in (session_review.directory() / "pending").glob("*.json"):
        path.unlink()
    _target(tmp_path, "unknown", None)
    assert run_skill_batch.dispatch(_args(), fail) == 124
    assert observed[-1] == json.loads((run_skill_batch.PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text())["repository"]
    assert len(list(layout.iter_entry_paths(notes))) == 2


def test_orphan_executor_prevents_new_model_before_pid_publish() -> None:
    """PID保存前の親中断でも固有manifestを持つ子が残れば再起動しない。"""
    path = session_review.directory() / "runs" / "orphan.json"
    manifest = {"run_id": "orphan", "manifest_path": str(path), "status": "running", "targets": [], "prepared": []}
    session_review.write(path, manifest)
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", f"batch manifest: {path}"]) as child:
        try:

            def unexpected(args):
                raise AssertionError("生存中の子と重複して起動しない")

            assert run_skill_batch.dispatch(_args(), unexpected) == 1
        finally:
            child.terminate()
            child.wait(timeout=10)


def _failure_awis(notes: pathlib.Path) -> list[pathlib.Path]:
    """失敗報告として保存されたAWIを返す。"""
    return [
        path
        for path in layout.iter_entry_paths(notes)
        if "session_review_failure_key:" in path.read_text(encoding="utf-8") and "type: awi" in path.read_text(encoding="utf-8")
    ]


def _fail_prepare_for(monkeypatch, names: set[str]) -> list[str]:
    """指定した対象の準備だけを、実記録で観測した未確認範囲の失敗と同じ形で失敗させる。"""
    original = run_skill_batch._prepare  # pylint: disable=protected-access
    calls: list[str] = []

    def prepare(target):
        calls.append(target["session_id"])
        if target["session_id"] in names:
            raise ValueError(f"prepareに未確認範囲がある: {target['session_id']}")
        return original(target)

    monkeypatch.setattr(run_skill_batch, "_prepare", prepare)
    return calls


def test_prepare_failure_isolates_target_and_runs_model_for_rest(tmp_path, monkeypatch) -> None:
    """1対象の準備失敗でも残りの対象でモデルを起動し、失敗対象だけを配布元への是正AWIとともに待ちへ残す。"""
    notes = _notes()
    _target(tmp_path, "one")
    _target(tmp_path, "bad")
    _fail_prepare_for(monkeypatch, {"bad"})
    calls = []

    def execute(args):
        manifest = session_review.read(pathlib.Path(args.batch_manifest))
        calls.append([target["session_id"] for target in manifest["targets"]])
        monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", args.run_id)
        monkeypatch.setenv(session_review.BATCH_ENV, args.batch_manifest)
        add.add_entries(
            notes,
            messages=["---\nrun_skill_report: true\n---\n# 振り返り結果\n\n1件を確認した。"],
            target_repo=args.target_repo,
            source="run-skill",
            now=datetime.datetime.now(datetime.UTC),
            entry_type="uwi",
            scope=session_review.BATCH_SKILL,
            question_type="post-approval",
        )
        assert run_script.dispatch(argparse.Namespace(script_name="session-review-state", script_args=["finish"])) == 0
        return 0

    assert run_skill_batch.dispatch(_args(), execute) == 1
    assert calls == [["one"]]
    assert [target["session_id"] for target in session_review.pending()] == ["bad"]
    failures = _failure_awis(notes)
    assert len(failures) == 1
    data, body = frontmatter.parse_frontmatter(failures[0].read_text(encoding="utf-8")) or ({}, "")
    # ユーザーの回答を待たず、振り返りの仕組みの配布元で処理担当が是正できるAWIとして登録する。
    assert data["type"] == "awi" and data["source"] == "session-review-batch"
    assert data["target_repo"] == run_skill_batch.repo.resolve_repo_id(
        json.loads((run_skill_batch.PLUGIN_ROOT / ".claude-plugin/plugin.json").read_text())["repository"]
    )
    assert "対象: bad\n" in body and "## 完成条件" in body


def test_manual_prepare_failure_does_not_retry_same_target(tmp_path, monkeypatch) -> None:
    """失敗中の対象も選ぶ手動実行で、同じ呼び出しの中では準備の失敗を繰り返さない。"""
    _notes()
    _target(tmp_path, "bad")
    calls = _fail_prepare_for(monkeypatch, {"bad"})

    def unexpected(args):
        raise AssertionError("準備できた対象が無いためモデルは起動しない")

    assert run_skill_batch.dispatch(_args("manual"), unexpected) == 1
    assert calls == ["bad"]
    assert session_review.pending()
