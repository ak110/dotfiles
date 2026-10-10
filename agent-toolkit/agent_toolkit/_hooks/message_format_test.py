"""実際の生成元の送出値を、閉じた配送境界と共通受信判定へ接続する。

CLI・委譲・hookとTS modの契約検証をここへ集約し、消費側ごとのテストへ同じ判定を転記しない。
TSの送出値取得はNodeのtype strippingで実ファイルを読み、外部ホストのAPIだけを代替する。
modのホスト契約自体はclaude plugin testが検証する。
"""

import argparse
import io
import json
import pathlib
import subprocess
import sys
import types
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from agent_toolkit._agents_server import manager, notify, shared_layout
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._atk import commit, run_skill
from agent_toolkit._atk.run_skill_progress import Progress
from agent_toolkit._atk.wi import process_loop_session
from agent_toolkit._common import automated_prompt, periodic_recheck
from agent_toolkit._hooks import pretooluse
from agent_toolkit._hooks.message_format import llm_notice
from agent_toolkit._testing.helpers import auto_message_opening_attributes


@pytest.mark.parametrize(
    ("tag", "expected_kind"),
    [
        pytest.param("", "notice", id="default"),
        pytest.param("warn", "warn", id="tagged"),
    ],
)
def test_llm_notice_wraps_body_with_xml_boundary(tag: str, expected_kind: str) -> None:
    """タグ有無にかかわらず出所、種別および本文を保つ。"""
    notice = llm_notice("本文", "example", tag=tag)
    assert auto_message_opening_attributes(notice) == {"source": "example", "kind": expected_kind}
    assert notice.endswith("\n本文\n</atk-auto>")


def test_llm_notice_escapes_attribute_values() -> None:
    """属性値にXMLメタ文字があっても境界を壊さない。"""
    notice = llm_notice("本文", '"example&', tag="warn")
    element = ET.fromstring(notice)
    assert element.attrib["source"] == '"example&'


def _assert_machine_boundary(text: str) -> dict[str, str]:
    """本文全体の境界と、ユーザー入力を消費する実際の共通判定を同じ値で確かめる。"""
    assert automated_prompt.contains(text)
    generated = text.partition("\n\n<forwarded-user-input ")[0].rstrip()
    assert generated.endswith("\n</atk-auto>")
    opening = generated.split("\n", 1)[0]
    opening = opening[opening.index("<atk-auto ") :]
    attributes = ET.fromstring(opening[:-1] + "/>").attrib
    assert attributes.get("source") and attributes.get("kind")
    return attributes


def test_process_loop_commit_hook_and_periodic_generators_share_the_receive_contract(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """正常な別生成元と定期promptも、受信側が機械本文の出所・用途と全体境界を区別できる。"""
    commit_body = commit._prompt(  # noqa: SLF001  # pylint: disable=protected-access
        root=tmp_path,
        format_instructions="日本語のcommit",
        staged_stat="",
        unstaged_stat="",
        untracked_names=[],
        amend=False,
        head_message="",
        dry_run=True,
        additional_prompt="ユーザーが書いた追加要求",
    )
    bodies = [
        process_loop_session.build_process_loop_prompt(),
        process_loop_session._AVAILABILITY_PROBE_PROMPT,  # noqa: SLF001  # pylint: disable=protected-access
        commit_body,
        llm_notice("定型の通知", "example"),
        llm_notice("別の原因", "another", tag="warn"),
        periodic_recheck.PERIODIC_RECHECK_PROMPT,
    ]
    assert pretooluse.main(json.dumps({"tool_name": "Bash", "tool_input": {"command": "atk wi list >/dev/null"}})) == 2
    bodies.append(capsys.readouterr().err)
    for body in bodies:
        _assert_machine_boundary(body)
    lines = periodic_recheck.PERIODIC_RECHECK_PROMPT.splitlines()
    lines.insert(-1, "測定コマンド: date +%s; 判定閾値: 3600秒")
    extended = "\n".join(lines)
    _assert_machine_boundary(extended)
    assert extended.split("\n", 1)[0] == periodic_recheck.PERIODIC_RECHECK_MARKER
    for broken in ("裸の機械通知", periodic_recheck.PERIODIC_RECHECK_MARKER + "\n終了境界が無い本文"):
        with pytest.raises(AssertionError):
            _assert_machine_boundary(broken)
    assert not automated_prompt.contains("通常の人間の発話")


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_run_skill_generated_probe_and_goal_reach_shared_receiver(
    engine: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run-skillが外部プロセスへ渡す可用性照会と目的文の、実際の送出値を取得する。"""
    bodies: list[str] = []

    def select(_candidates: object, _env: object, _root: object, context: Any, **_kwargs: object) -> tuple[str, str, str]:
        bodies.append(context.prompt)
        return engine, "model", "medium"

    original = subprocess.Popen

    def launch(argv: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        bodies.append(argv[-1])
        # 外部モデルの起動だけを短いPythonプロセスへ代替し、実際の待機・出力回収を通す。
        return original([sys.executable, "-c", "print('OK')"], **kwargs)

    monkeypatch.setattr(run_skill._orchestrator, "select_available", select)  # noqa: SLF001  # pylint: disable=protected-access
    monkeypatch.setattr(subprocess, "Popen", launch)
    log = io.StringIO()
    result = run_skill._run_locked(  # noqa: SLF001  # pylint: disable=protected-access
        argparse.Namespace(skill="agent-toolkit:search", args="対象", timeout=30),
        repo_root=tmp_path,
        candidates=[(engine, "model", "medium")],
        log=log,
        log_path=tmp_path / "session.log",
        progress=Progress(log, io.StringIO()),
    )
    assert result == 0
    assert len(bodies) == 2
    for body in bodies:
        attributes = _assert_machine_boundary(body)
        assert attributes["source"] == "run-skill"


@pytest.mark.asyncio
async def test_start_followup_and_auto_resume_generated_deliveries_reach_shared_receiver(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開始・追送・子孫終端・過負荷・利用上限の各生成元の送出値を、backend境界で取得する。"""
    owner = manager.AgentsServerManager()
    bodies: list[str] = []

    async def candidates(*_args: object, **_kwargs: object) -> tuple[list[tuple[str, str, str]], dict]:
        return [("codex", "gpt-6.1-sol", "medium")], {}

    async def initialize(engine: str, body: str, cwd: str, *_args: object, **_kwargs: object) -> SessionState:
        bodies.append(body)
        session = SessionState("contract-session", cwd, engine=engine)
        session.status = "running"
        session.model_output_observed = True
        owner.sessions[session.session_id] = session
        return session

    async def deliver(_session: SessionState, body: str, **_kwargs: object) -> dict[str, str]:
        bodies.append(body)
        return {"delivery": "steered"}

    # 候補解決とモデル起動・配送の外部I/Oを代替し、各操作自身による本文生成を通す。
    monkeypatch.setattr(owner, "_resolve_start_candidates", candidates)
    monkeypatch.setattr(owner, "_start_until_initialized", initialize)
    monkeypatch.setattr(owner, "_backend", lambda _engine: types.SimpleNamespace(send_message=deliver))
    started = await owner.start("high_tier", "開始本文", str(tmp_path))
    assert started["status"] == "running"
    await owner.send_message("contract-session", "追送本文")
    session = owner.sessions["contract-session"]
    for operation in ("children", "overload", "usage-limit"):
        session.pending_result = {
            "status": "completed",
            "agent_message": "待機中",
            "error": {"message": "失敗", "usageLimit": {"type": "five_hour"}},
        }
        session.awaiting_auto_resume = True
        if operation == "children":
            session.terminal_child_session_ids.add("child-session")
            await owner._advance_child_session_wait(session)  # noqa: SLF001  # pylint: disable=protected-access
        elif operation == "overload":
            await owner._resume_after_overload(session)  # noqa: SLF001  # pylint: disable=protected-access
        else:
            await owner._resume_after_usage_limit(session)  # noqa: SLF001  # pylint: disable=protected-access
    assert len(bodies) == 5
    for body in bodies:
        _assert_machine_boundary(body)


def test_notify_saved_delivery_reaches_shared_receiver(tmp_path: pathlib.Path) -> None:
    """委譲元への保存済み通知も、実際に送出するbodyの境界・送信元を保持する。"""
    assert (
        notify.send_notification(
            "通知本文", environment={"AGENT_TOOLKIT_OWNER_SESSION": "parent", "CODEX_THREAD_ID": "child"}, state_root=tmp_path
        )
        == 0
    )
    saved = list(shared_layout.notices_directory("parent", tmp_path).glob("*.json"))
    assert len(saved) == 1
    body = json.loads(saved[0].read_text(encoding="utf-8"))["body"]
    attributes = _assert_machine_boundary(body)
    assert attributes["source"] == "agents-notify" and attributes["from"] == "delegate:child"


def test_typescript_mod_emitted_values_reach_python_shared_receiver(tmp_path: pathlib.Path) -> None:
    """実modの失敗通知・装着通知と別入力の包装を、Pythonの実際の受信判定へ渡す。"""
    hooks = pathlib.Path(__file__).resolve().parents[2] / "hooks"
    script = tmp_path / "mod-deliveries.mjs"
    script.write_text(
        """const {register: compact} = await import(process.argv[2]);
const {register: periodic} = await import(process.argv[3]);
const {automatedMessage} = await import(process.argv[4]);
const schedule = JSON.parse(process.argv[5]);
const bodies = [];
function registration() {
  const calls = [];
  const on = (event, selector, callback) => {
    if (event === 'tool.call') calls.push(callback ?? selector);
    return {catch() { return this; }};
  };
  return {on, calls};
}
const c = registration();
compact(c.on);
const timers = [];
const base = {plugin: {name: 'agent-toolkit', root: '/plugin'}, env: {get: async () => undefined}};
await c.calls[0]({...base, clock: {after: (_time, callback) => timers.push(callback)},
  command: {run: async () => { throw new Error('圧縮の実行拒否'); }},
  prompt: {submit: async ({text}) => bodies.push(text)}}, {tool: 'mcp__agent-toolkit__compact_conversation'}, async () => ({}));
await timers[0]();
for (const success of [true, false]) {
  const p = registration();
  periodic(p.on);
  const result = await p.calls[0]({...base,
    tool: {check: async () => ({decision: 'allow'}), call: async ({tool}) => tool === 'CronList'
      ? {result: {jobs: []}} : {result: {id: 'task-1'}}},
    process: {run: async () => ({exitCode: success ? 0 : 1, stdout: JSON.stringify(schedule), stderr: success ? '' : '失敗'})}},
    {tool: 'mcp__plugin_agent-toolkit_agents_server__start'}, async () => ({result: '開始結果'}));
  bodies.push(...result.context);
}
bodies.push(automatedMessage('sender&"<>\\n', 'notice', '別の本文\\r\\n<atk-auto>'));
process.stdout.write(JSON.stringify(bodies));
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "node",
            str(script),
            (hooks / "compact_conversation.ts").as_uri(),
            (hooks / "periodic_recheck.ts").as_uri(),
            (hooks / "automated_message.ts").as_uri(),
            json.dumps({"cron": "*/3 * * * *", "prompt": periodic_recheck.PERIODIC_RECHECK_PROMPT}),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    bodies = json.loads(result.stdout)
    assert len(bodies) == 4
    attributes = [_assert_machine_boundary(body) for body in bodies]
    assert attributes[0] == {"source": "compact-conversation", "kind": "warn"}
    assert [item["kind"] for item in attributes[1:3]] == ["notice", "warn"]
    assert attributes[3]["source"] == 'sender&"<>\n'
