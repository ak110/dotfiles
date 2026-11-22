import argparse
import contextlib
import io
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import state as server_state
from agent_toolkit._atk import run_script
from agent_toolkit._common import automated_prompt, session_state
from agent_toolkit._hooks import posttooluse, pretooluse, termination_evidence, user_prompt_submit
from agent_toolkit._testing import agents_server_support

CONTINUATION = '<atk-auto source="stop" kind="continuation">判断を記録する</atk-auto>'


COMPLETION_PLAYBOOK_TEXT = "# 作業完了報告とセッションの完了処理\n\nこの手順書を全文読むことが終了工程の開始である。\n"


class _WindowsAccessDenied(PermissionError):
    """Windowsの`os.replace`が返すWinError 5と同じ識別値を持つ置換拒否。"""

    winerror = 5


@pytest.fixture(autouse=True)
def isolated_session(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    for key in (
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    (tmp_path / "transcript.jsonl").touch()
    root = tmp_path / "norms/agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    playbook = root / "skills/atk-mode/playbooks/report-completion.md"
    playbook.parent.mkdir(parents=True)
    playbook.write_text(COMPLETION_PLAYBOOK_TEXT, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", str(root))


def _tool(
    directory: pathlib.Path,
    call_id: str,
    tool_name: str,
    tool_input: dict,
    response: object,
    *,
    codex: bool,
) -> None:
    payload: dict[str, Any] = {
        "session_id": "evidence-test",
        "tool_name": tool_name,
        "tool_use_id": call_id,
        "transcript_path": str(directory / "transcript.jsonl"),
        "cwd": str(directory),
        "tool_input": tool_input,
    }
    if codex:
        payload["turn_id"] = "codex-turn"
    with contextlib.redirect_stdout(io.StringIO()):
        pretooluse.main(json.dumps(payload))
    payload["tool_response"] = response
    with contextlib.redirect_stdout(io.StringIO()):
        posttooluse.main(json.dumps(payload))


def supply_commit(directory: pathlib.Path, call_id: str = "commit", *, codex: bool = False) -> None:
    response: object = "[develop 1234567] 変更" if codex else {"stdout": "[develop 1234567] 変更", "exit_code": 0}
    _tool(directory, call_id, "Bash", {"command": "git commit -m 変更"}, response, codex=codex)


def completion_playbook(directory: pathlib.Path) -> pathlib.Path:
    playbook = directory / "plugin" / "skills" / "atk-mode" / "playbooks" / "report-completion.md"
    playbook.parent.mkdir(parents=True, exist_ok=True)
    playbook.write_text(COMPLETION_PLAYBOOK_TEXT, encoding="utf-8")
    manifest = directory / "plugin/.claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    return playbook


def claude_read_response(path: pathlib.Path, *, offset: int = 1, limit: int | None = None) -> dict[str, Any]:
    """Claude CodeのReadのPostToolUseの`tool_response`を、指定した行範囲で返す。

    形はClaude Code 2.1.303の本体の結果（`type`・`file`）から写した。
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    selected = lines[offset - 1 : None if limit is None else offset - 1 + limit]
    file = {
        "filePath": str(path),
        "content": "".join(selected),
        "numLines": len(selected),
        "startLine": offset,
        "totalLines": len(lines),
    }
    return {"type": "text", "file": file}


def supply_completion(directory: pathlib.Path, call_id: str = "completion", *, codex: bool = False) -> None:
    """完了報告の起動証拠を、Claude Codeは入口の起動の成功、Codexは手順書の読取で供給する。"""
    if codex:
        playbook = completion_playbook(directory)
        _tool(directory, call_id, "Bash", {"command": f"cat {playbook}"}, COMPLETION_PLAYBOOK_TEXT, codex=True)
    else:
        _tool(
            directory,
            call_id,
            "Skill",
            {"skill": "agent-toolkit:atk-mode", "args": "report-completion"},
            "起動",
            codex=False,
        )


def human_input(directory: pathlib.Path, turn_id: str, prompt: str) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "turn_id": turn_id,
                    "prompt": prompt,
                    "transcript_path": str(directory / "transcript.jsonl"),
                }
            )
        )


def stop_payload(directory: pathlib.Path, visible: str, *, codex: bool = False) -> str:
    payload: dict[str, Any] = {
        "session_id": "evidence-test",
        "transcript_path": str(directory / "transcript.jsonl"),
        "stop_hook_active": False,
        "last_assistant_message": visible,
    }
    if codex:
        payload["turn_id"] = "codex-turn"
    return json.dumps(payload)


def _missing_completion(directory: pathlib.Path) -> list[str]:
    return [
        kind
        for _, work in termination_evidence.pending_work(json.loads(stop_payload(directory, "")))
        for kind in termination_evidence.missing_completion(work)
    ]


def automated_input(directory: pathlib.Path, prompt_id: str, prompt: str) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "prompt_id": prompt_id,
                    "prompt": prompt,
                    "transcript_path": str(directory / "transcript.jsonl"),
                }
            )
        )


def automated_session(directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    automated_input(
        directory,
        "goal",
        automated_prompt.wrap("WIを処理する", source=automated_prompt.SOURCE_PROCESS_LOOP, kind=automated_prompt.KIND_GOAL),
    )
    supply_commit(directory)
    automated_input(directory, "continuation", CONTINUATION)


def recorded_works() -> list[tuple[str, dict[str, Any]]]:
    return termination_evidence.session_works({"session_id": "evidence-test"})


def public_context(capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any] | None]:
    capsys.readouterr()
    status = run_script.dispatch(argparse.Namespace(script_name="termination-evidence", script_args=["--", "--context"]))
    output = capsys.readouterr().out
    return status, json.loads(output) if status == 0 else None


def public_decision(directory: pathlib.Path, document: dict[str, Any]) -> tuple[int, str]:
    source = directory / "decision.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    stderr = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
        status = run_script.dispatch(
            argparse.Namespace(script_name="termination-evidence", script_args=["--decision-file", str(source)])
        )
    return status, stderr.getvalue()


def _omission_document(repository: pathlib.Path, input_id: str | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "session_id": "evidence-test",
        "action": "omit-completion",
        "work_id": "work-1",
        "reason": "適用規範に従う",
        "policy_file": str(repository / "CLAUDE.md"),
        "target_repo": str(repository),
    }
    if input_id is not None:
        document["input_id"] = input_id
    return document


def _queued_command(directory: pathlib.Path, uuid: str, prompt: str, origin: str) -> None:
    """ターンの途中に届いた入力を、Claude Codeのセッション記録の`queued_command`の形で追記する。"""
    entry = {
        "type": "attachment",
        "uuid": uuid,
        "attachment": {"type": "queued_command", "prompt": prompt, "origin": {"kind": origin}},
    }
    with (directory / "transcript.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _register_child(owner: str) -> None:
    def register(state: dict) -> dict:
        state.setdefault("agents_server_sessions", {})["child-1"] = {"owner_agent_id": owner, "pending_observation": True}
        return state

    session_state.update_state("evidence-test", register)


class _RegisteredBackend(agents_server_support.FakeBackend):
    async def start(self, *args: Any, **kwargs: Any) -> server_state.SessionState:
        session = await super().start(*args, **kwargs)
        session.model_output_observed = True
        session.publish_registry = True
        return session
