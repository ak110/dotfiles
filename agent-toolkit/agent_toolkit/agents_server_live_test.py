"""agents_serverの実backendによる自動再開とCodex接続先の切替を検証する。

各ケースの入力と、その入力の実行に要るツールは`LiveInput`で起動区分ごとに宣言する。
宣言と起動設定の整合は課金不要の`agents_server_live_contract_test.py`が判定する。
"""

import collections.abc
import dataclasses
import http.server
import json
import pathlib
import threading
from typing import Any

import pytest

from agent_toolkit._agents_server import codex_providers, mcp_tools
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server.state import LaunchKind
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing import isolation

pytestmark = pytest.mark.live_agents


@dataclasses.dataclass(frozen=True)
class LiveInput:
    """live testが1つの起動区分へ渡す入力と、委譲先がその入力を実行するのに要るClaude Codeの組込ツール。

    `texts`は委譲先へ届く本文（起動時と継続時の依頼、シェル実行の要約方針）を並べる。
    `required_tools`は入力が要求する操作から決まり、起動区分の能力から逆算しない。
    """

    launch_kind: LaunchKind
    texts: tuple[str, ...]
    required_tools: frozenset[str]


# Bash背景実行の完了通知による自動再開を確かめる入力。Bashを公開する起動区分だけへ渡す。
BACKGROUND_RESUME_INPUTS: dict[str, LiveInput] = {
    "delegate": LiveInput(
        "delegate",
        (
            """Bashツールで`sleep 2`を背景実行し、待たずにturnを終えよ。
バックグラウンドタスクの完了通知で自動的に再開したturnでは、最終応答を`AUTO_RESUME_COMPLETED`だけにせよ。""",
        ),
        frozenset({"Bash"}),
    ),
    "shell": LiveInput(
        "shell",
        ("Bashツールを背景実行し、完了通知で再開した後に`AUTO_RESUME_COMPLETED`だけを返す。",),
        frozenset({"Bash"}),
    ),
}

# 読取限定の探索起動へ渡す入力。新規起動と同じsessionへの継続の双方で既知のファイルを読ませる。
# 読ませる内容は本文へ書かず、ファイルを読まなければ応答できない形にする。
EXPLORE_READ_INPUT = LiveInput(
    "explore",
    (
        "Readツールで`{path}`を読み、そのファイルの内容だけを最終応答にせよ。",
        "Readツールで`{path}`を読み、そのファイルの内容だけを最終応答にせよ。",
    ),
    frozenset({"Read"}),
)


async def _wait_terminal(manager: server_manager.AgentsServerManager) -> dict[str, Any]:
    """待機上限へ達した応答は`running`を返すため、終端まで同じ待機を繰り返す。"""
    result = await manager.wait()
    for _ in range(10):
        if result["status"] != "running":
            break
        result = await manager.wait()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(BACKGROUND_RESUME_INPUTS))
async def test_live_launch_waits_for_automatic_resume(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bashを公開する起動ツール`start`のmodeが、再開指示なしでバックグラウンドタスク完了後の結果を返す。"""
    manager = server_manager.AgentsServerManager()
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    cwd = str(pathlib.Path(__file__).parents[2])
    # 委譲先CLIが認証と設定を読み、PATHから起動できるよう、ホームと設定ディレクトリとPATHを戻す。
    isolation.restore_host_environment(monkeypatch)
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type: [("claude", "sonnet[1m]", "medium")],
    )
    live_input = BACKGROUND_RESUME_INPUTS[mode]
    try:
        if mode == "delegate":
            await mcp_tools.start(cwd, mode="delegate", prompt=live_input.texts[0], model_type="low_tier")
        else:
            await mcp_tools.start(cwd, mode="shell", command="sleep 2", summary_policy=live_input.texts[0])

        result = await manager.wait()

        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "AUTO_RESUME_COMPLETED"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_live_explore_reads_file_and_continues_same_session(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """探索起動が既知のファイルを読んで内容を返し、同じsessionへの継続でも別のファイルを読んで返す。"""
    manager = server_manager.AgentsServerManager()
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    # 作業ディレクトリの外の読取は権限確認を要するため、読ませるファイルを置く一時ディレクトリをcwdにする。
    first = tmp_path / "first.txt"
    first.write_text("EXPLORE_FIRST_CONTENT\n", encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("EXPLORE_CONTINUED_CONTENT\n", encoding="utf-8")
    # 委譲先CLIが認証と設定を読み、PATHから起動できるよう、ホームと設定ディレクトリとPATHを戻す。
    isolation.restore_host_environment(monkeypatch)
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type: [("claude", "sonnet[1m]", "medium")],
    )
    start_text, continue_text = EXPLORE_READ_INPUT.texts
    try:
        started = await mcp_tools.start(str(tmp_path), mode="explore", prompt=start_text.format(path=first))
        session = manager.sessions[started["session_id"]]

        result = await _wait_terminal(manager)
        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "EXPLORE_FIRST_CONTENT", result

        reply = await mcp_tools.send_message(started["session_id"], continue_text.format(path=second))
        assert reply["delivery"] == "reply_started", reply

        result = await _wait_terminal(manager)
        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "EXPLORE_CONTINUED_CONTENT", result
        assert session.turn_seq == 2
    finally:
        await manager.close()


# 孫sessionの終端による自動再開を確かめる入力。MCPの起動ツールと`atk agents wait`の実行を要求するため、
# 組込ツールとMCPを制限しない通常起動へ渡す。
GRANDCHILD_INPUT = LiveInput(
    "delegate",
    (
        """`agents_server`の起動ツールで、コマンド`sleep 5`を実行して終了状態を要約させる委譲を1件起動せよ。
起動ツールが`mode`を受け取る場合は`start`へ`mode`の`shell`を渡し、受け取らない場合は`start_shell`を使う。
起動の直後は`atk agents wait`を実行せず、`待機中: <起動したsession_id>`の1行だけを出力してターンを終えよ。
自動的に再開したターンでは`atk agents wait`でその委譲の結果を回収し、最終応答を`AUTO_RESUME_COMPLETED`だけにせよ。""",
    ),
    frozenset({"Bash"}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", [("codex", "sol", "medium"), ("claude", "sonnet[1m]", "medium")])
async def test_live_grandchild_wait_resumes_same_session(
    candidate: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """委譲先が孫sessionを起動して待機を表明すると、孫の終端後に同じsessionが手動の指示なしに再開し完了報告を返す。

    待機表明の行を完了報告として配送せず、再開したturnの結果だけを委譲元へ返すことを確かめる。
    """
    manager = server_manager.AgentsServerManager()
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    cwd = str(pathlib.Path(__file__).parents[2])
    # 委譲先CLIが認証と設定を読み、PATHから起動できるよう、ホームと設定ディレクトリとPATHを戻す。
    isolation.restore_host_environment(monkeypatch)
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [candidate])
    try:
        started = await mcp_tools.start(cwd, mode="delegate", prompt=GRANDCHILD_INPUT.texts[0], model_type="high_tier")
        session = manager.sessions[started["session_id"]]

        result = await _wait_terminal(manager)

        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "AUTO_RESUME_COMPLETED", result
        assert "unobservedSessions" not in (result.get("error") or {}), result
        assert session.turn_seq == 2
    finally:
        await manager.close()


# 擬似APIの接続先名とテスト用のAPIキーの環境変数。後続候補の判定（`codex_providers.independent_api_auth`）は
# `env_key`の環境変数が設定済みであることを独立したAPI認証として扱う。
_PRIMARY_PROVIDER = "pseudo_primary"
_FALLBACK_PROVIDER = "pseudo_fallback"
_PSEUDO_API_KEY_ENV = "AGENT_TOOLKIT_LIVE_TEST_PSEUDO_API_KEY"
_SWITCH_TASK_MARKER = "PROVIDER_SWITCH_TASK"
_PRE_SWITCH_RESPONSE = "PRE_SWITCH_RESPONSE"
_ENCRYPTED_REASONING = "PSEUDO_ENCRYPTED_REASONING"
_TOOL_CALL_ID = "call_provider_switch"
# コマンドの実行結果にだけ現れる文字列。引数の文字列と区別するため、実行時に連結させる。
_TOOL_OUTPUT = "PROVIDER_SWITCH_TOOL_OUTPUT"
_TOOL_COMMAND = "printf 'PROVIDER_SWITCH_%s' TOOL_OUTPUT"
_SWITCH_COMPLETED = "PROVIDER_SWITCH_COMPLETED"


class _PseudoResponsesApi:
    """loopbackだけで待ち受け、受けた`/responses`要求の本文を接続先ごとに記録するResponses API互換の擬似API。

    主接続先はツール呼び出しを返し、その実行結果を含む要求へ利用上限のHTTP 429を返す。
    後続接続先は最終応答を返してturnを完了させる。
    応答の形はCodex CLI 0.162.1が解釈するResponses APIのSSEイベント
    （`response.created`・`response.output_item.done`・`response.completed`）と、
    同版が`usageLimitExceeded`へ分類する429本文（`error.type`が`usage_limit_reached`）に合わせる。
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()
        api = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandlerが呼ぶ名前
                parts = self.path.strip("/").split("/")
                if len(parts) != 3 or parts[1:] != ["v1", "responses"]:
                    self.send_error(404)
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                api.record(parts[0], body)
                if parts[0] == _PRIMARY_PROVIDER and _has_item(body, "function_call_output"):
                    payload = json.dumps(
                        {"error": {"type": "usage_limit_reached", "message": "The usage limit has been reached"}}
                    ).encode()
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if parts[0] == _PRIMARY_PROVIDER:
                    output = [
                        {
                            "type": "reasoning",
                            "id": "rs_provider_switch",
                            "summary": [],
                            "encrypted_content": _ENCRYPTED_REASONING,
                        },
                        _assistant_message(_PRE_SWITCH_RESPONSE),
                        _tool_call(body),
                    ]
                else:
                    output = [_assistant_message(_SWITCH_COMPLETED)]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                events = [
                    {"type": "response.created", "response": {"id": "resp_provider_switch"}},
                    *({"type": "response.output_item.done", "item": item} for item in output),
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_provider_switch",
                            "usage": {
                                "input_tokens": 0,
                                "input_tokens_details": None,
                                "output_tokens": 0,
                                "output_tokens_details": None,
                                "total_tokens": 0,
                            },
                        },
                    },
                ]
                for event in events:
                    self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

            def log_message(self, *args: Any, **kwargs: Any) -> None:
                del args, kwargs

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def base_url(self, provider: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/{provider}/v1"

    def record(self, provider: str, body: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append((provider, body))

    def bodies(self, provider: str) -> list[dict[str, Any]]:
        with self._lock:
            return [body for name, body in self.requests if name == provider]

    def __enter__(self) -> "_PseudoResponsesApi":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join()


def _has_item(body: dict[str, Any], item_type: str) -> bool:
    return any(item.get("type") == item_type for item in body.get("input", []))


def _assistant_message(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "id": f"msg_{text.lower()}",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _tool_call(body: dict[str, Any]) -> dict[str, Any]:
    """要求が公開したシェル系ツールの引数形式で、`_TOOL_COMMAND`を実行させる呼び出しを返す。"""
    names = {tool.get("name") for tool in body.get("tools", [])}
    if "exec_command" in names:
        name, arguments = "exec_command", {"cmd": _TOOL_COMMAND}
    elif "shell_command" in names:
        name, arguments = "shell_command", {"command": _TOOL_COMMAND}
    else:
        name, arguments = "shell", {"command": ["bash", "-lc", _TOOL_COMMAND]}
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": _TOOL_CALL_ID}


def _message_texts(items: collections.abc.Iterable[dict[str, Any]], role: str) -> list[str]:
    return [
        "".join(part.get("text", "") for part in item.get("content", []))
        for item in items
        if item.get("type") == "message" and item.get("role") == role
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("share_package_caches")
async def test_live_codex_provider_switch_resends_history(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """利用上限で失敗したCodex会話が同じthreadのまま後続接続先へ切り替わり、切替前の履歴を再送して完了する。

    隔離した`CODEX_HOME`へ擬似APIを指す2つの接続先を置き、実際の`codex app-server`を`start`から起動する。
    切替後の接続先が受けた最初の要求に、切替前の入力・応答・推論項目・ツール呼び出しと実行結果および続行指示が
    含まれなければ、後続接続先は履歴を失った会話を続けることになる。
    """
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    with _PseudoResponsesApi() as api:
        providers = "".join(
            f"""
[model_providers.{name}]
name = "{name}"
base_url = "{api.base_url(name)}"
wire_api = "responses"
env_key = "{_PSEUDO_API_KEY_ENV}"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
"""
            for name in (_PRIMARY_PROVIDER, _FALLBACK_PROVIDER)
        )
        (codex_home / "config.toml").write_text(providers, encoding="utf-8")
        # miseのshimがホストの設定を読めるよう、HOME・設定ディレクトリ・PATHをまとめて戻す。
        isolation.restore_host_environment(monkeypatch)
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        monkeypatch.setenv(_PSEUDO_API_KEY_ENV, "pseudo-api-key")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS", f"{_PRIMARY_PROVIDER},{_FALLBACK_PROVIDER}")
        monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [("codex", "gpt-5.4", "low")])
        manager = server_manager.AgentsServerManager()
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        try:
            await mcp_tools.start(
                str(work),
                mode="delegate",
                prompt=f"{_SWITCH_TASK_MARKER}: シェルでコマンドを1回実行して結果を報告せよ。",
                model_type="low_tier",
            )
            result = await manager.wait()
            # 待機上限へ達した応答は`running`を返すため、終端まで同じ待機を繰り返す。
            for _ in range(10):
                if result["status"] != "running":
                    break
                result = await manager.wait()
        finally:
            await manager.close()
        primary = api.bodies(_PRIMARY_PROVIDER)
        fallback = api.bodies(_FALLBACK_PROVIDER)

    assert result["status"] == "completed", result
    assert result["agent_message"].strip() == _SWITCH_COMPLETED, result
    # 主接続先はツール実行結果を含む要求で上限に達した。
    assert primary, api.requests
    assert _has_item(primary[-1], "function_call_output"), primary[-1]
    assert fallback, api.requests
    items = fallback[0]["input"]
    user_texts = _message_texts(items, "user")
    assert any(_SWITCH_TASK_MARKER in text for text in user_texts), user_texts
    assert _PRE_SWITCH_RESPONSE in _message_texts(items, "assistant"), items
    assert any(item.get("type") == "reasoning" and item.get("encrypted_content") == _ENCRYPTED_REASONING for item in items), (
        items
    )
    assert any(item.get("type") == "function_call" and item.get("call_id") == _TOOL_CALL_ID for item in items), items
    outputs = [item for item in items if item.get("type") == "function_call_output" and item.get("call_id") == _TOOL_CALL_ID]
    assert outputs, items
    assert _TOOL_OUTPUT in json.dumps(outputs[0].get("output"), ensure_ascii=False), outputs
    # 続行指示は切替前の履歴の後に置かれた最後のuser入力である。
    assert codex_providers.CONTINUE_PROMPT in user_texts[-1], user_texts
