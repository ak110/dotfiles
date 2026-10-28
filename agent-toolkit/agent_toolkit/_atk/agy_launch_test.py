"""agyの公開CLI契約（実機agy --help）へ、各ランチャーの入力を接続する。"""

import argparse
import json
import os
import pathlib
import stat
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit._atk import orchestrator, run_skill
from agent_toolkit._atk.wi import add as wi_add
from agent_toolkit._atk.wi import process_loop_env, process_loop_log, process_loop_session


def test_agy_probe_works_without_tier_settings(tmp_path: pathlib.Path) -> None:
    """工程別のagy候補は、agyの段位設定を作らずに可用性を判定できる。"""
    binary = tmp_path / "agy"
    captured = tmp_path / "probe.json"
    binary.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        f"pathlib.Path({str(captured)!r}).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    candidate = ("agy", "gemini-4-argon", "medium")
    assert (
        orchestrator.select_available([candidate], env, tmp_path, orchestrator.ProbeContext(title="test", prompt="可用性確認"))
        == candidate
    )
    argv = json.loads(captured.read_text())
    assert argv[argv.index("-p") + 1] == "可用性確認"
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash"
    assert argv[argv.index("--mode") + 1] == "plan"
    assert "--dangerously-skip-permissions" not in argv


@pytest.mark.parametrize("resume", [None, "", "saved-conversation"])
def test_process_loop_agy_interactive_and_resume(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, resume: str | None
) -> None:
    """process-loopは新規目的文、直近継続、ID付き再開をagyの対話CLIへ渡す。"""
    calls: list[list[str]] = []

    def execute(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        assert kwargs["cwd"] == tmp_path
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(process_loop_session.subprocess, "run", execute)

    def launch(argv: list[str], env: dict[str, str], cwd: pathlib.Path, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return execute(argv, env=env, cwd=cwd)

    monkeypatch.setattr(process_loop_session, "_run_cli_session", launch)
    monkeypatch.setattr(process_loop_session, "cleanup_lane_session", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(process_loop_env, "reset_console", lambda: None)
    monkeypatch.setattr(process_loop_log, "append", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process_loop_log, "consume_instructions", lambda: "")
    args = argparse.Namespace(resume=resume, no_update=True)
    manifest = tmp_path / "current-manifest.json"
    process_loop_session.run_process_session(
        args,
        tmp_path,
        process_loop_session.build_process_loop_prompt(manifest),
        {},
        orchestrator="agy",
        model="gemini-4-argon",
        effort="medium",
        resume_pending=resume is not None,
    )
    argv = calls[0]
    assert argv[0] == "agy"
    assert argv[argv.index("--model") + 1] == "gemini-4-argon"
    assert argv[argv.index("--effort") + 1] == "medium"
    delivered = argv[argv.index("-i") + 1]
    assert "agent-toolkit:process-wi-all-repos" in delivered
    assert str(manifest) in delivered
    assert not delivered.startswith("/goal ")
    if resume:
        assert argv[argv.index("--conversation") + 1] == resume
    elif resume is not None:
        assert "--continue" in argv


def test_run_skill_agy_noninteractive(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """定期起動はagy -pで目的文を送り、成功時は報告保存の既存検収まで通す。"""
    binary = tmp_path / "agy"
    captured = tmp_path / "session.json"
    binary.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "prompt = args[args.index('-p') + 1]\n"
        "if 'availability-probe' not in prompt:\n"
        f"    pathlib.Path({str(captured)!r}).write_text(json.dumps(args))\n"
        "print(json.dumps({'event': 'init', 'conversation_id': 'scheduled-conversation'}))\n"
        "print(json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'response': '完了'}}))\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(orchestrator, "resolve_specs", lambda **_kwargs: [("agy", "gemini-4-argon", "medium")])
    monkeypatch.setattr(run_skill, "resolve_local_worktree", lambda _target: tmp_path)
    monkeypatch.setattr(run_skill, "_resolve_repo_root", lambda path: path)
    monkeypatch.setattr(wi_add, "has_synced_run_report", lambda *_args, **_kwargs: True)
    assert run_skill.dispatch(argparse.Namespace(timeout=30, target_repo=str(tmp_path), skill="test:skill", args=None)) == 0
    argv = json.loads(captured.read_text())
    assert "スキル`test:skill`を完遂" in argv[argv.index("-p") + 1]
    assert argv[argv.index("--model") + 1] == "gemini-4-argon"
    assert argv[argv.index("--effort") + 1] == "medium"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--print-timeout") + 1] == "30s"
