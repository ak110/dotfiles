"""process-loopの公開入口、単回実行とホストへの配送を検証する。"""

import argparse
import html
import io
import json
import pathlib
import subprocess
import sys
import threading
import time
from typing import Any

import pytest

from agent_toolkit import atk, hook
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop as loop
from agent_toolkit._atk.wi import process_loop_control as control
from agent_toolkit._atk.wi import process_loop_env as env
from agent_toolkit._atk.wi import process_loop_log as log
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._testing.process_loop_coordinator_support import _manifest, _targets


def test_public_loop_checks_eight_repositories_with_bounded_concurrency(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """8対象を最大3並行で確認し、結果は登録順で表示して候補を一度だけ再取得する。"""
    _notes, snapshots = _targets(tmp_path, monkeypatch, (0,) * 8)
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    lock = threading.Lock()
    group = threading.Barrier(3)
    active = maximum = 0
    seen = []
    durations = []
    timings = []
    parallel = False

    def check(_args, _notes, repo_id, _worktree, _last, **kwargs):
        nonlocal active, maximum
        assert kwargs["count_dependabot"]
        with lock:
            active += 1
            maximum = max(maximum, active)
            seen.append(repo_id)
            ordinal = len(seen)
        started = time.monotonic()
        timings.append(started)
        if parallel and ordinal <= 6:
            group.wait(timeout=5)
        # 同じAPI待機を逐次実行した場合の総待機時間と比較する。
        time.sleep(0.05)
        with lock:
            active -= 1
            durations.append(time.monotonic() - started)
            timings.append(time.monotonic())
        return 1.0, 1, frozenset()

    monkeypatch.setattr(loop._alerts, "check_process_loop_alerts", check)  # pylint: disable=protected-access
    original = listing.configured_repo_snapshot
    reads = []

    def snapshot(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(listing, "configured_repo_snapshot", snapshot)
    pool = loop.concurrent.futures.ThreadPoolExecutor
    elapsed = []
    for parallel in (False, True):
        seen.clear()
        timings.clear()
        durations.clear()
        reads.clear()
        maximum = 0
        with monkeypatch.context() as scoped:
            scoped.setattr(
                loop.concurrent.futures,
                "ThreadPoolExecutor",
                lambda workers=3 if parallel else 1, **_kwargs: pool(max_workers=workers),
            )
            with pytest.raises(SystemExit) as exited:
                atk.main(["wi", "process-loop", "--no-update", "--once"], home=tmp_path)
        elapsed.append(max(timings) - min(timings))
        assert exited.value.code == 0
        assert maximum == (3 if parallel else 1)
        assert set(seen) == {item.repository.repo_id for item in snapshots} and len(seen) == 8
        assert len(reads) == 2
        output = capsys.readouterr().out
        positions = [output.index(f"アラート監視により{item.repository.repo_id}") for item in snapshots]
        assert positions == sorted(positions)
    assert elapsed[1] < elapsed[0]
    print(f"同じ8対象の照会区間: 逐次={elapsed[0]:.3f}秒、最大3並行={elapsed[1]:.3f}秒")


@pytest.mark.parametrize("failure", [OSError("照会用ファイルを読めない"), ValueError("応答の形式が不正")])
@pytest.mark.parametrize("once", [False, True])
def test_public_loop_alert_failure_has_diagnostics_and_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: Exception, once: bool
) -> None:
    """アラート照会例外でも常駐を継続し、対象付きの診断取得と再確認を案内する。"""
    _notes, snapshots = _targets(tmp_path, monkeypatch, (0,))
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    checks = iter((False, True))
    monkeypatch.setattr(control, "consume_process_loop_abort", lambda: next(checks))
    monkeypatch.setattr(loop, "_wait_for_changes", lambda *_args, **_kwargs: False)

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(loop._alerts, "check_process_loop_alerts", fail)  # pylint: disable=protected-access
    with pytest.raises(SystemExit) as exited:
        atk.main(["wi", "process-loop", "--no-update", *(["--once"] if once else [])], home=tmp_path)
    assert exited.value.code == 0
    output = capsys.readouterr()
    text = output.out + output.err
    assert str(failure) in text
    assert "次の操作:" in text
    assert str(log.log_path()) in text
    assert "atk wi check-alerts --target-repo" in text
    assert str(snapshots[0].repository.worktree) in text
    assert "再確認" in text
    assert ("今回処理できる対象は0件" if once else "変更検知を待機") in text
    assert "repository_alert_failed" in log.log_path().read_text(encoding="utf-8")


@pytest.mark.parametrize("status", ["completed", "incomplete", "blocked", "owner_wait", "prepare_failed"])
def test_public_once_finishes_one_collection(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """1集合の未完了継続と所有判定を保ち、次のWI集合を起動しない。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    calls = 0

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        data = json.loads(_manifest(prompt, child_env).read_text(encoding="utf-8"))
        child_env[env.PROCESS_LOOP_SESSION_ID_ENV] = "once-coordinator"
        kwargs["on_session_started"]("once-coordinator")
        if status == "completed":
            (notes / "2026/10/repo-0-0.md").unlink()
            (notes / "2026/10/new.md").write_text(
                "---\nstate: inbox\ntype: awi\ntarget_repo: example.test/team/repo-0\n---\n\n# 次の集合\n", encoding="utf-8"
            )
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {snapshots[0].repository.repo_id: {"status": status}}}), encoding="utf-8"
        )
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    if status == "prepare_failed":
        monkeypatch.setattr(loop._worktree, "prepare_session_target", lambda *_args, **_kwargs: None)  # pylint: disable=protected-access
    owner = (
        ownership.try_acquire(snapshots[0].repository.repo_id, worktree=snapshots[0].repository.worktree)
        if status == "owner_wait"
        else None
    )
    try:
        with pytest.raises(SystemExit) as exited:
            atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update"], home=tmp_path)
        assert exited.value.code == (0 if status == "completed" else 1)
        assert calls == (0 if status in {"owner_wait", "prepare_failed"} else 1)
    finally:
        if owner is not None:
            ownership.release(owner)


@pytest.mark.parametrize("counts", [(), (0, 0)])
def test_public_once_empty_does_not_wait(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, counts: tuple[int, ...]
) -> None:
    """登録対象の候補0件ではモデル・待機・所有を開始しない。"""
    _targets(tmp_path, monkeypatch, counts)
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("候補0件で待機または起動した")

    monkeypatch.setattr(loop, "_wait_for_changes", unexpected)
    monkeypatch.setattr(session, "run_process_session", unexpected)
    with pytest.raises(SystemExit) as exited:
        atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update"], home=tmp_path)
    assert exited.value.code == 0


@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
@pytest.mark.parametrize("resume", [False, True])
def test_public_host_delivers_current_manifest(tmp_path, monkeypatch, capsys, engine, resume) -> None:
    """明示再開でも今回の配送原本と結果保存先を使用し、対象別の完了を検収する。"""
    notes, snapshots = _targets(tmp_path, monkeypatch, (1,))
    repository = snapshots[0].repository
    owner = ownership.try_acquire(repository.repo_id, worktree=repository.worktree, mode="coordinator")
    assert owner is not None
    owner = ownership.update_session(owner, "saved-agy-conversation")
    ownership.release(owner, completed=False)
    monkeypatch.setattr(loop._watch, "pull_private_notes", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access
    monkeypatch.setattr(
        session, "select_available_orchestrator", lambda *_args, **_kwargs: (engine, "model", "medium", None, None)
    )
    manifests: list[dict[str, Any]] = []

    def execute(
        argv: list[str], child_env: dict[str, str], _cwd: pathlib.Path, **_kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        if resume:
            assert "saved-agy-conversation" in argv or "--resume=saved-agy-conversation" in argv
        prompt = argv[-1]
        expected = (
            '<atk-auto source="process-loop" kind="goal">\n'
            "このセッションが統括として担当するagent-toolkit:process-wi all-reposの工程を完遂する"
            "（正当な先送りを結果へ記録した終了を含み、終了後に常駐が行う検収は含まない）\n</atk-auto>"
        )
        assert prompt == ("/goal " if engine == "claude" else "") + expected
        manifest = _manifest(prompt, child_env)
        capsys.readouterr()
        with monkeypatch.context() as scoped:
            scoped.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", child_env["AGENT_TOOLKIT_SESSION_START_INPUT"])
            source = "resume" if resume else "startup"
            if engine == "agy":
                # agy 1.4.4はSessionStartの通知を次のInvocationで配送する。
                payload = {
                    "conversationId": "manifest-delivery",
                    "workspacePaths": [str(tmp_path)],
                    "transcriptPath": str(tmp_path / "agy.jsonl"),
                    "source": source,
                }
                for event in ("SessionStart", "PreInvocation"):
                    scoped.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8"))))
                    assert hook.main(["antigravity", event]) == 0
                    response = json.loads(capsys.readouterr().out)
                context = "\n\n".join(step["ephemeralMessage"] for step in response["injectSteps"])
            else:
                payload = json.dumps({"hook_event_name": "SessionStart", "source": source}).encode("utf-8")
                scoped.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
                assert hook.main(["rules_context_codex" if engine == "codex" else "rules_context"]) == 0
                context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
        assert 'kind="startup-input"' in context
        assert str(manifest) in html.unescape(context)
        assert "references/all-repos.md「結果と終了」" in context
        data = json.loads(manifest.read_text(encoding="utf-8"))
        manifests.append(data)
        target = data["repositories"][0]
        stored = json.loads(pathlib.Path(target["ownership_record"]).read_text(encoding="utf-8"))
        assert stored["manifest"] == str(manifest)
        (notes / "2026/10/repo-0-0.md").unlink()
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {repository.repo_id: {"status": "completed"}}}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(session, "_run_cli_session", execute)
    with pytest.raises(SystemExit) as exited:
        atk.main(
            [
                "wi",
                "process-loop",
                "--once",
                *(["--resume", "saved-agy-conversation"] if resume else []),
                "--no-alerts",
                "--no-update",
            ],
            home=tmp_path,
        )
    assert exited.value.code == 0
    assert len(manifests) == 1
    assert pathlib.Path(manifests[0]["result_file"]).is_file()
    stored = json.loads(owner.path.read_text(encoding="utf-8"))
    assert stored["released"] and stored["completed"]


@pytest.mark.parametrize("failure", ["sync", "start_abort", "interrupt"])
def test_public_once_failure_does_not_wait(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """1回処理の同期失敗・開始前中断・Ctrl+Cは成功にせず、再試行待機も始めない。"""
    _targets(tmp_path, monkeypatch, (1,))
    monkeypatch.setattr(loop._update, "resolve_dotfiles_root", lambda: None)  # pylint: disable=protected-access

    def pull(*_args: Any, **_kwargs: Any) -> bool:
        if failure == "interrupt":
            raise KeyboardInterrupt
        return failure != "sync"

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("失敗・中断後に待機またはモデルを起動した")

    monkeypatch.setattr(loop._watch, "pull_private_notes", pull)  # pylint: disable=protected-access
    monkeypatch.setattr(loop._watch, "wait_for_start_permission", lambda *_args, **_kwargs: False)  # pylint: disable=protected-access
    monkeypatch.setattr(loop, "_wait_for_changes", unexpected)
    monkeypatch.setattr(session, "run_process_session", unexpected)
    with pytest.raises(SystemExit) as exited:
        atk.main(["wi", "process-loop", "--once", "--no-alerts", "--no-update"], home=tmp_path)
    assert exited.value.code == 1
