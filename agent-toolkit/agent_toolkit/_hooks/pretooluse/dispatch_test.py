# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatchの契約テスト。"""

import io
import itertools
import json
import pathlib
import re
import shlex
import sys
from collections.abc import Callable

import pytest

from agent_toolkit import atk, hook
from agent_toolkit._common import session_state
from agent_toolkit._hooks.pretooluse import agent_checks, user_facing_checks
from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._testing.dispatch_support import _assert_plain_pass
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract
from agent_toolkit._testing.hook_output_contract import validate_hook_output
from agent_toolkit._testing.pretooluse_support import _plan_file_state_env, _run, _write_session_state


@pytest.mark.parametrize(
    ("command", "protected"),
    [
        ("atk config temporary set orchestrate_model codex:sol/medium", True),
        ("atk config temporary set codex_fast_mode auto", True),
        ("atk config temporary clear", True),
        ("atk config set review_model codex:sol", True),
        ("atk config set codex_worker_connections subscription", True),
        ("atk config apply-preset codex-primary", True),
        ("atk config apply-preset --list", False),
        ("atk config apply-preset show", False),
        ("atk config show", False),
        ("atk config set codex_fast_mode auto", False),
        ('atk config update <<\'JSON\'\n{"action":"temporary-clear"}\nJSON', True),
        ('atk config update <<\'JSON\'\n{"action":"set","key":"codex_fast_mode","value":"auto"}\nJSON', False),
        ("atk config update < request.json", True),
        ("cat request.json | atk config update", True),
        ('atk config update <<JSON\n{"action":"set","key":"$KEY","value":"auto"}\nJSON', True),
    ],
)
@pytest.mark.parametrize("is_codex", [False, True])
def test_model_setting_command_uses_host_permission(command, protected, is_codex, capsys):
    tool_input = {"command": command}
    payload = {"session_id": "permission-test", "tool_input": tool_input}
    decision = pretooluse._decide(payload, "Bash", tool_input, "permission-test", is_codex=is_codex)
    assert pretooluse._emit(decision, [], is_codex=is_codex) == 0
    output = capsys.readouterr().out
    actual = json.loads(output)["hookSpecificOutput"].get("permissionDecision") if output else None
    assert actual == (("deny" if is_codex else "ask") if protected else None)


@pytest.mark.parametrize(("is_codex", "approve", "writes"), [(False, True, True), (False, False, False), (True, True, False)])
def test_host_decision_controls_actual_local_model_save(tmp_path, monkeypatch, capsys, is_codex, approve, writes):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "missing"))
    setting = "claude:opus[1m]/medium"
    data = {"command": f"atk config set review_model {setting}"}
    payload = {"session_id": "permission-test", "tool_input": data}
    decision = pretooluse._decide(payload, "Bash", data, "permission-test", is_codex=is_codex)
    assert pretooluse._emit(decision, [], is_codex=is_codex) == 0
    permission = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"]
    assert permission == ("deny" if is_codex else "ask")
    if permission == "ask" and approve:
        with pytest.raises(SystemExit) as result:
            atk.main(["config", "set", "review_model", setting], home=tmp_path)
        assert result.value.code == 0
    with pytest.raises(SystemExit) as result:
        atk.main(["config", "get", "review_model"], home=tmp_path)
    assert result.value.code == 0
    assert (capsys.readouterr().out.splitlines()[-1] == setting) is writes


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("tool_name", sorted(agent_checks.AGENTS_SERVER_HOOK_TOOL_NAMES))
def test_agents_server_pass_uses_host_output_contract(tmp_path: pathlib.Path, host: str, tool_name: str) -> None:
    """全agents_server通過の生成箇所が、ホスト別の公開hook契約へ到達する。"""
    _write_session_state(
        tmp_path,
        "host-pass",
        {
            "agents_server_cwd_by_session": {"remote": str(tmp_path)},
        },
    )
    result = _run(
        {
            "tool_name": tool_name,
            "tool_input": {"session_id": "remote", "prompt": "続行", "cwd": str(tmp_path)},
            "session_id": "host-pass",
            **({"turn_id": "codex-turn"} if host == "codex" else {}),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    output = json.loads(result.stdout) if result.stdout else {}
    assert not validate_hook_output("PreToolUse", output, host=host)
    if host == "codex":
        assert not output
    else:
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "tool_input",
    [
        {"session_id": "remote", "prompt": "続行する"},
        {"session_id": "remote", "instruction": "保存開始", "extra_params": {"要求識別子": "r-1"}},
        {"session_id": "remote", "instruction": "再レビューせよ", "extra_params": {"round": "2"}},
    ],
    ids=["free-prompt", "save-start", "rereview"],
)
def test_continuation_inputs_pass_without_input_warning(tmp_path: pathlib.Path, host: str, tool_input: dict) -> None:
    _write_session_state(
        tmp_path,
        "host-continuation",
        {
            "agents_server_cwd_by_session": {"remote": str(tmp_path)},
        },
    )
    result = _run(
        {
            "tool_name": "mcp__agents_server__send_message",
            "tool_input": tool_input,
            "session_id": "host-continuation",
            **({"turn_id": "codex-turn"} if host == "codex" else {}),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    _assert_plain_pass(result, host)


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize(
    "operation,tool_input",
    [
        ("send_message", {"session_id": "remote"}),
        ("send_message", {"prompt": "続行"}),
        ("kill", {}),
    ],
)
def test_continuation_format_errors_pass_to_tool(tmp_path: pathlib.Path, host: str, operation: str, tool_input: dict) -> None:
    _write_session_state(
        tmp_path,
        "host-format",
        {
            "agents_server_cwd_by_session": {"remote": str(tmp_path)},
        },
    )
    result = _run(
        {
            "tool_name": f"mcp__agents_server__{operation}",
            "tool_input": tool_input,
            "session_id": "host-format",
            **({"turn_id": "codex-turn"} if host == "codex" else {}),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    _assert_plain_pass(result, host)


def _question_decision(tmp_path: pathlib.Path, question: dict, **payload: str) -> str:
    result = _run(
        {"tool_name": "AskUserQuestion", "session_id": "question-check", "tool_input": {"questions": [question]}, **payload},
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout or "{}")
    if output.get("decision") == "block":
        return output["reason"]
    return output.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


@pytest.mark.parametrize("codex", [False, True])
def test_recommendation_passes_first_time(tmp_path: pathlib.Path, codex: bool) -> None:
    question = {"question": "進めてよいですか？", "options": [{"label": "進める", "description": "推奨する案"}]}
    host = {"turn_id": "codex-turn", "tool_name": "request_user_input"} if codex else {}
    assert not _question_decision(tmp_path, question, **host)
    assert not _question_decision(tmp_path, question, **host)
    assert not _question_decision(tmp_path, {**question, "options": [{"label": "進める"}]}, **host)
    assert not _question_decision(tmp_path, question, agent_id="child", **host)


_APPROVAL_OPTIONS = [
    {"label": "この方針で進める", "description": "送った方針のとおりに着手する"},
    {"label": "修正して進める", "description": "修正点を自由記述で伝える"},
]
_APPROVAL = {"header": "方針の承認", "question": "送った2件目の方針で進めてよいですか？", "options": _APPROVAL_OPTIONS}


@pytest.mark.parametrize(
    "question",
    [
        {
            **_APPROVAL,
            "question": "送った方針で進めてよいですか？",
            "options": [
                {"label": "この方針で進める（推奨）", "description": "送った方針のとおりに着手する"},
                {"label": "Aだけにする", "description": "Bを外す"},
                _APPROVAL_OPTIONS[1],
            ],
        },
        {
            **_APPROVAL,
            "question": "A: bugfixへ実例を載せて既存の失敗例を置き換える、B: 記録の種類を必須にする、"
            "Cとして1件のAWIで登録する方針で進めてよいですか？",
        },
        {**_APPROVAL, "question": "送ったA・Bの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送ったBを外す方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送ったA、Bの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った1.の方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った［Aだけ］の方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った旧版を除くの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った外すの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った廃止しないの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った廃止せずの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った廃止せぬの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った廃止したの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った動けるの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った廃止されるの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送ったできるの方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った［修正版］の方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った修正版、追記の方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った今回分最終版の方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った前回分改訂版の方針で進めてよいですか？"},
        {**_APPROVAL, "question": "\n送った方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った方針で進めてよいですか？\n"},
        {**_APPROVAL, "question": "送った\n方針で進めてよいですか？"},
        {**_APPROVAL, "question": "送った方針で進めてよいですか？\nA: 実例を載せる"},
        {
            **_APPROVAL,
            "options": [_APPROVAL_OPTIONS[0], {"label": "修正して進める", "description": "Bを外し、Aだけを登録する"}],
        },
        {**_APPROVAL, "options": [{**_APPROVAL_OPTIONS[0], "label": "この方針で進める (Recommended)"}, _APPROVAL_OPTIONS[1]]},
        {**_APPROVAL, "multiSelect": True},
        {**_APPROVAL, "header": "確認", "question": "上の方針（A・B）で進めてよいか？"},
    ],
)
@pytest.mark.parametrize("codex", [False, True])
def test_policy_approval_accepts_varied_form(tmp_path: pathlib.Path, question: dict, codex: bool) -> None:
    host = {"turn_id": "codex-turn", "tool_name": "request_user_input"} if codex else {}
    for _ in range(2):
        decision = _question_decision(tmp_path, question, **host)
        assert not decision


def test_policy_approval_allows_additional_question(tmp_path: pathlib.Path) -> None:
    result = _run(
        {
            "tool_name": "AskUserQuestion",
            "session_id": "question-check",
            "tool_input": {"questions": [_APPROVAL, {"question": "どの系統へ登録しますか？", "options": _APPROVAL_OPTIONS}]},
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0
    assert "permissionDecisionReason" not in result.stdout


@pytest.mark.parametrize(
    "modifier",
    [
        "",
        "2件目の",
        "２回目の",
        "二十一件目の",
        "修正版の",
        "改訂版の",
        "再送版の",
        "最新版の",
        "まとめ版の",
        "最終版の",
        "機能廃止の",
        "初回送付版の",
        "再送改訂版の",
        "統合版の",
        "差し替え版の",
    ],
)
@pytest.mark.parametrize("codex", [False, True])
def test_fixed_policy_approval_passes_first_time(tmp_path: pathlib.Path, codex: bool, modifier: str) -> None:
    host = {"turn_id": "codex-turn", "tool_name": "request_user_input"} if codex else {}
    question = {**_APPROVAL, "question": f"送った{modifier}方針で進めてよいですか？"}
    assert not _question_decision(tmp_path, question, **host)


@pytest.mark.parametrize("tool", ["AskUserQuestion", "request_user_input", "request_user_input_async"])
@pytest.mark.cpu_limit(1.0, reason="各ホストの公開hook入口と再承認前後の全操作を同一セッションで通す。")
def test_policy_reapproval_blocks_only_until_new_matching_approval(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tool: str
) -> None:
    monkeypatch.setattr(session_state, "state_path", lambda sid: tmp_path / f"{sid}.json")
    # 再承認と独立した活動記録が実ホストのプロセス祖先をたどる費用を除く。  pyfltr: allow-comment
    monkeypatch.setattr(hook, "_record_activity", lambda *_args, **_kwargs: None)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    private_notes = tmp_path / "notes"
    dated = private_notes / "2026" / "11"
    dated.mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    for kind, filename in (("awi", "20261126-095854-001.md"), ("uwi", "20261126-095854-002.md")):
        (dated / filename).write_text(f"---\ntype: {kind}\nstate: inbox\n---\n# 確認\n", encoding="utf-8")
    approved = "この方針で進める"
    revise = "大体よいが、入力ごとにAWI相当を作るように修正して"
    asynchronous = tool == "request_user_input_async"
    session = "approval"
    host = {} if tool == "AskUserQuestion" else {"turn_id": "codex-turn"}
    hook_path = pathlib.Path(__file__).resolve().parents[3] / "hooks" / "hooks.codex.json"
    hooks = json.loads(hook_path.read_text(encoding="utf-8"))["hooks"]

    def invoke(event: str, name: str, payload: dict) -> None:
        entry = {"PreToolUse": "pretooluse", "PostToolUse": "posttooluse", "UserPromptSubmit": "user_prompt_submit"}[event]
        if host and name in {"", tool, "Bash", "mcp__agents_server__send_to_user"}:
            registered = {
                shlex.split(handler["command"].split(" || ", 1)[0].split(" && ")[-1])[-1]
                for group in hooks[event]
                if re.fullmatch(group.get("matcher", ".*"), name)
                for handler in group["hooks"]
            }
            assert entry in registered
        with monkeypatch.context() as stdin, io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())) as stream:
            stdin.setattr(sys, "stdin", stream)
            assert hook.main([entry]) == 0

    def pre(name: str, body: dict, sid: str = session, **extra: object) -> str:
        capsys.readouterr()
        payload = {
            "cwd": str(tmp_path),
            "hook_event_name": "PreToolUse",
            "session_id": sid,
            "tool_name": name,
            "tool_input": body,
            **host,
            **extra,
        }
        invoke("PreToolUse", name, payload)
        text = capsys.readouterr().out.strip()
        result = json.loads(text) if text else {}
        if result.get("decision") == "block":
            return result["reason"]
        scoped = result.get("hookSpecificOutput", {})
        return scoped.get("permissionDecisionReason", "") if scoped.get("permissionDecision") == "deny" else ""

    def prompt(text: str) -> None:
        invoke(
            "UserPromptSubmit",
            "",
            {
                "session_id": session,
                "hook_event_name": "UserPromptSubmit",
                "prompt": text,
                **host,
            },
        )

    def post(tool_name: str, payload: dict, body: dict, response: dict) -> None:
        invoke(
            "PostToolUse",
            tool_name,
            {
                **payload,
                **host,
                "session_id": session,
                "hook_event_name": "PostToolUse",
                "tool_name": tool_name,
                "tool_input": body,
                "tool_response": response,
            },
        )

    def ask(call: str, modifier: str = "") -> tuple[dict, dict]:
        question = f"送った{modifier}方針で進めてよいですか？"
        options = list(_APPROVAL_OPTIONS)
        item = (
            {"title": f"方針の承認：{question}", "options": [option["label"] for option in options]}
            if asynchronous
            else {"header": "方針の承認", "question": question, "options": options, "id": "policy"}
        )
        payload = {"tool_use_id": call}
        body = {"questions": [item]}
        assert not pre(tool, body, **payload)
        if asynchronous:
            post(tool, payload, body, {})
        return payload, body

    def answer(asked: tuple[dict, dict], value: str, *, index: int | float = 0) -> None:
        payload, body = asked
        if asynchronous:
            replies = [{"questionItemId": json.dumps([tool, payload["tool_use_id"], index]), "answer": value}]
            prompt(f"<send_user_message_question_reply>{json.dumps(replies)}</send_user_message_question_reply>")
        else:
            answers = (
                {"policy": {"answers": [value]}} if tool == "request_user_input" else {body["questions"][0]["question"]: value}
            )
            post(tool, payload, body, {"answers": answers})

    def send(message: str, *, ok: bool = True) -> None:
        post("mcp__agents_server__send_to_user", {}, {"message": message}, {"structuredContent": {"ok": ok}})

    commands = (
        "git commit -m test",
        "atk run-script commit-with-timeout -- --timeout=10 -- -m test",
        "atk wi add --body-file=draft.md",
        "atk wi edit 20261126-095854-001.md --body-file=draft.md",
    )

    def blocked(command: str, shell: str = "Bash", sid: str = session, payload: dict | None = None) -> str | None:
        reason = pre(shell, {"command": command}, sid, **(payload or {}))
        return reason or None

    malformed = (
        {"title": "方針の承認：送った方針で進めてよいですか？", "options": []}
        if asynchronous
        else {"header": "方針の承認", "options": []}
    )
    assert not pre(tool, {"questions": [malformed]})

    ordinary = (
        {"questions": [{"title": "確認", "options": ["はい"]}]}
        if asynchronous
        else {"questions": [{"header": "確認", "question": "確認しますか", "id": "policy", "options": []}]}
    )
    assert not pre(tool, ordinary, tool_use_id="ordinary")
    if asynchronous:
        post(tool, {"tool_use_id": "ordinary"}, ordinary, {})
    answer(({"tool_use_id": "ordinary"}, ordinary), revise)
    assert not blocked(commands[0])

    answer(ask("approval-before"), approved)
    if asynchronous:
        answer(({"tool_use_id": "missing"}, {}), revise)
    assert not blocked(commands[0])
    answer(ask("choice-two"), "修正して進める")
    plain_reason = blocked(commands[0])
    assert plain_reason and "実回答" in plain_reason
    send("方針（案）")
    answer(ask("cleared-plain"), approved)
    answer(ask("revision", "2件目の"), revise)
    modified_reason = blocked(commands[0])
    assert modified_reason and "適用対象" in modified_reason and "実回答" in modified_reason
    for shell in ("Bash", "exec_command", "functions.exec_command"):
        for command in commands:
            assert "問い直" in (blocked(command, shell) or "")
    for command in (
        "atk wi add --dry-run",
        "atk wi edit 20261126-095854-001.md --body-file=draft.md --dry-run",
        "atk wi add --type=uwi",
        "atk wi edit 20261126-095854-002.md --body-file=draft.md",
        "atk wi start-processing awi.md",
    ):
        assert not blocked(command)
    assert "方針の修正回答に対する再承認" not in pre("spawn_agent" if host else "Agent", {"prompt": "読み取り"})
    with monkeypatch.context() as delegated:
        delegated.setenv("AGENT_TOOLKIT_OWNER_SESSION", session)
        for command in commands:
            assert "委譲元" in (blocked(command, sid="started-before") or "")
    for command in commands:
        assert "委譲元" in (blocked(command, payload={"agent_id": "child"}) or "")
    assert not blocked(commands[0], sid="unrelated", payload={"agent_id": "child"})
    (tmp_path / "copied.json").write_bytes((tmp_path / f"{session}.json").read_bytes())
    assert not blocked(commands[0], sid="copied")
    send("方針の改訂内容は定型の見出しなしで説明する。")
    answer(ask("unrelated"), approved)
    prompt(approved)
    assert blocked(commands[0])
    old = ask("old", "2件目の")
    answer(ask("new-revision", "2件目の"), revise)
    answer(old, approved)
    assert blocked(commands[0])
    issued = ask("new-approval", "2件目の")
    if asynchronous:
        answer(({"tool_use_id": "missing"}, {}), approved)
        assert blocked(commands[0])
        for index in (False, 0.0, 1):
            answer(issued, approved, index=index)
            assert blocked(commands[0]), (type(index), index)
    answer(issued, approved)
    for command in commands:
        assert not blocked(command)


def test_policy_reapproval_bounded_event_order(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    session = "bounded"
    tick = 0

    def update(_session: str, mutate: Callable[[dict], dict | None]) -> bool:
        return mutate(state) is not None

    def now() -> int:
        return tick

    checks = user_facing_checks
    monkeypatch.setattr(checks, "update_state", update)
    monkeypatch.setattr(checks.time, "time_ns", now)
    name = "AskUserQuestion"
    labels = ["この方針で進める", "修正して進める"]
    modifiers = ("", "2件目の")
    checked = 0
    for (revision, approval, delivery), second_wait in itertools.product(itertools.product(modifiers, repeat=3), (False, True)):
        other = next(modifier for modifier in modifiers if modifier != revision)
        revision_text = f"送った{revision}方針で進めてよいですか？"
        approval_text = f"送った{approval}方針で進めてよいですか？"

        def issue(call: str, text: str) -> None:
            body = {
                "questions": [
                    {
                        "header": "方針の承認",
                        "question": text,
                        "options": list(_APPROVAL_OPTIONS),
                    }
                ]
            }
            checks.record_policy_question({"tool_use_id": call}, session, name, body)

        def answer(call: str, text: str, value: str) -> None:
            checks.observe_policy_result({"tool_use_id": call, "tool_response": {"answers": {text: value}}}, session, name, {})

        for order in itertools.permutations(("revise", "deliver", "issue", "approve")):
            if order.index("issue") > order.index("approve"):
                continue
            state.clear()
            tick = 0
            issue("revision", revision_text)
            if second_wait:
                other_text = f"送った{other}方針で進めてよいですか？"
                issue("other-revision", other_text)
                answer("other-revision", other_text, labels[1])
            for tick, event in enumerate(order, start=1):
                if event == "revise":
                    answer("revision", revision_text, labels[1])
                elif event == "deliver":
                    checks.observe_policy_result(
                        {"tool_response": {"ok": True}},
                        session,
                        "mcp__agents_server__send_to_user",
                        {"message": f"方針（案） {delivery}".rstrip()},
                    )
                elif event == "issue":
                    issue("approval", approval_text)
                else:
                    answer("approval", approval_text, labels[0])
                history = order[:tick]
                expected = "revise" in history and not (
                    revision == approval
                    and all(action in history for action in ("revise", "issue", "approve"))
                    and order.index("revise") < order.index("issue") < order.index("approve")
                )
                actual = state[checks._APPROVAL_STATE].get("waits", {})
                remaining = {revision} if expected else set()
                if second_wait and not (other == approval and {"issue", "approve"}.issubset(history)):
                    remaining.add(other)
                assert set(actual) == remaining, (revision, delivery, approval, history)
            checked += 1
    assert checked == 192


@pytest.mark.parametrize("tool", ["AskUserQuestion", "ExitPlanMode", "mcp__agents_server__send_to_user"])
@pytest.mark.parametrize(
    "text",
    [
        "発話の理解: 原文: 対象を確認した。",
        "既存の基準で足りるため規範は足さない。",
        "形式検証の判定という見出しはないが、保証範囲と費用を比較した。",
    ],
)
def test_quality_wording_passes_without_first_rejection(tool: str, text: str, capsys: pytest.CaptureFixture[str]) -> None:
    inputs = (
        {"questions": [{"question": text}]}
        if tool == "AskUserQuestion"
        else {"plan" if tool == "ExitPlanMode" else "message": text}
    )
    payload = {"session_id": "quality-wording", "turn_id": "turn", "tool_name": tool, "tool_input": inputs}
    assert pretooluse.main(json.dumps(payload)) == 0
    output = capsys.readouterr().out
    result = json.loads(output) if output else {}
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
