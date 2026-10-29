"""agents_serverの実backendによる自動再開とCodex接続先の切替を検証する。

各ケースの入力と、その入力の実行に要るツールは`LiveInput`で起動区分ごとに宣言する。
宣言と起動設定の整合は課金不要の`agents_server_live_contract_test.py`が判定する。
"""

import asyncio
import collections.abc
import dataclasses
import hashlib
import http.server
import json
import os
import pathlib
import shlex
import sys
import threading
import uuid
from typing import Any

import pytest

from agent_toolkit._agents_server import codex_providers, mcp_tools, record_paths
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server.state import LaunchKind
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import session_record_format
from agent_toolkit._testing import isolation
from agent_toolkit._testing.git_repository import init_repository, run_git

pytestmark = pytest.mark.live_agents


@pytest.fixture(autouse=True)
def _detach_caller_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    """エージェントの会話から実行しても、その会話の全体作業契約や記録先へ起動結果を結び付けない。

    通常委譲の起動は所有会話のmanaged-tempに全体作業契約が無いと拒否するため、呼出元の会話を継承すると
    試験の成否が呼出元の状態で変わる。端末から実行した場合と同じく所有会話を持たない状態にそろえる。
    """
    for name in ("AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(name, raising=False)


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


COMMON_INVESTIGATION_INPUT = LiveInput(
    "explore",
    (
        """今回のturnで隔離した対象を読み、以下の5操作をそれぞれ実際のツール呼出で新たに実行せよ。
同じsessionの継続でも、今回の5操作全てを再実行し、今回の実結果を返せ。
対象成果物・入力資料・ユーザーデータを作成・変更・削除しない。ツール管理のキャッシュ・状態・保存出力は区別する。
1. コマンド {git} を実行し、HEADとGit状態を取得する。
2. コマンド {rg} を実行し、該当行番号と本文を取得する。
3. コマンド {python} を実行し、Python標準ライブラリで分割取得した本文を得る。
4. コマンド {sed} を実行し、行範囲の本文を全行取得する。
5. 読取MCPクライアントのコマンド {mcp} を実行する。
入力資料のクライアントはpyfltr MCPサーバーとstdioで接続する。
grepへpaths=[{file_json}]、pattern=P4_READ_、fixed_strings=true、summary_mode=count、no_exclude=trueを渡す。
最終応答はhead、git_status、rg_lines、python_text、sed_text、mcp_countを持つJSONオブジェクトだけにする。
headはOID、git_statusは文字列、rg_linesは行番号付きの文字列2件の配列、python_textとsed_textは全文、mcp_countは整数とする。""",
    ),
    frozenset({"Bash"}),
)

_MCP_READER = """import asyncio
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    spec = json.loads(sys.argv[2])
    params = StdioServerParameters(command=spec["command"], args=spec["args"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool(
                "grep",
                {"paths": [sys.argv[1]], "pattern": "P4_READ_", "fixed_strings": True,
                 "summary_mode": "count", "no_exclude": True}
            )
            assert not result.is_error, result
            value = {"server": "pyfltr", "transport": "stdio", "tool": "grep", "result": result.model_dump(mode="json")}
            print(json.dumps(value, ensure_ascii=False))

asyncio.run(main())
"""


def _investigation_snapshot(repository: pathlib.Path) -> dict[str, Any]:
    """入力集合の全バイトとGitの版・index・worktreeを比較可能な値へ写す。"""
    return {
        "files": {
            str(p.relative_to(repository)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(repository.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(repository).parts
        },
        "head": run_git(repository, "rev-parse", "HEAD").stdout.strip(),
        "index": run_git(repository, "ls-files", "--stage").stdout,
        "status": run_git(repository, "status", "--porcelain=v1", "--untracked-files=all").stdout,
        "diff": run_git(repository, "diff", "--binary", "HEAD").stdout,
    }


def _investigation_events(session_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    """今回起動したsessionの実ツール記録を、各backendの既存の保存先から読む。"""
    record = record_paths.find_session_record(session_id)
    assert record is not None, f"sessionの実記録がない: {session_id}"
    rows = []
    for path in record.paths:
        parsed, broken = session_record_format.parse_records(path.read_text(encoding="utf-8"))
        assert not broken, path
        rows.extend(parsed)
    if record.engine == "agy":
        tools = [row for row in rows if "tool" in json.dumps(row.get("step_type", row.get("step_update", {})))]
    else:
        tools = [
            {**event.to_json(), "call_id": event.call_id}
            for event in session_record_format.record_events(record.engine, rows)
            if event.kind in {"tool_call", "tool_result"}
        ]
    return [str(path) for path in record.paths], tools


@pytest.mark.asyncio
@pytest.mark.timeout(900)
@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
async def test_live_explore_common_investigation(engine: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ調査を全backendの新規・同session再開で実行し、実ツール結果と対象不変を確認する。"""
    text = "P4_READ_ALPHA\n日本語の本文 " + uuid.uuid4().hex + "\nP4_READ_BETA\n"
    repository = init_repository(
        tmp_path / "target",
        files={"input/読取 資料.txt": text, "input/pyfltr_mcp_read.py": _MCP_READER},
        commit_message="調査入力を固定する",
    )
    source = repository / "input/読取 資料.txt"
    before = _investigation_snapshot(repository)
    # 認証情報はホストの通常の解決を使い、サーバー状態はテストfixtureの隔離先を保つ。
    isolation.restore_host_environment(monkeypatch)
    models = {"claude": "claude:sonnet[1m]/medium", "codex": "codex:sol/medium", "agy": "agy:gemini-3.8-flash/medium"}
    python_code = f"import pathlib; s=pathlib.Path({str(source)!r}).read_text(encoding='utf-8'); print(s[:7]+s[7:], end='')"
    mcp_spec = json.loads((pathlib.Path(__file__).parents[1] / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["pyfltr"]
    prompt = COMMON_INVESTIGATION_INPUT.texts[0].format(
        git=(
            f"git -C {shlex.quote(str(repository))} rev-parse HEAD と "
            f"git -C {shlex.quote(str(repository))} status --porcelain=v1"
        ),
        rg=f"rg -n -F P4_READ_ {shlex.quote(str(source))}",
        python=f"{shlex.quote(sys.executable)} -c {shlex.quote(python_code)}",
        sed=f"sed -n '1,$p' -- {shlex.quote(str(source))}",
        mcp=" ".join(
            shlex.quote(value)
            for value in (sys.executable, str(repository / "input/pyfltr_mcp_read.py"), str(source), json.dumps(mcp_spec))
        ),
        file_json=json.dumps(str(source), ensure_ascii=False),
    )
    async with server_manager.AgentsServerManager() as manager:
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        observed: dict[str, Any] = {
            "engine": engine,
            "model_type": models[engine],
            "prompt": prompt,
            "before": before,
            "turns": [],
        }
        destination = (
            pathlib.Path(os.environ.get("AGENT_TOOLKIT_LIVE_EVIDENCE_DIR", str(tmp_path))) / f"investigation-{engine}.json"
        )
        session_id = None
        try:
            for turn in (1, 2):
                async with asyncio.timeout(360):
                    if turn == 1:
                        response = await mcp_tools.start(
                            str(repository), mode="explore", prompt=prompt, model_type=models[engine]
                        )
                        session_id = response["session_id"]
                    else:
                        assert session_id is not None
                        response = await mcp_tools.send_message(session_id, prompt=prompt)
                        assert response["delivery"] == "reply_started", response
                    observed["session_id"] = session_id
                    observed["last_response"] = response
                    result = await _wait_terminal(manager)
                entry: dict[str, Any] = {"turn": turn, "session_id": session_id, "response": response, "result": result}
                observed["turns"].append(entry)
                assert result["status"] == "completed", result
                assert session_id is not None
                session = manager.sessions[session_id]
                assert session.engine == engine
                entry["turn_seq"] = session.turn_seq
                if turn == 2:
                    assert session.turn_seq == observed["turns"][0]["turn_seq"] + 1
                entry["record_paths"], entry["tool_events"] = _investigation_events(session_id)
                prior_count = observed["turns"][0].get("event_count", 0) if turn == 2 else 0
                entry["event_count"] = len(entry["tool_events"])
                actual_events = entry["tool_events"][prior_count:]
                tool_text = json.dumps(actual_events, ensure_ascii=False)
                for command in ("git", "rg", "sed -n", "python", "pyfltr"):
                    assert command in tool_text, (command, actual_events)
                # CLIが引数を省略した実記録でも、MCPの実返却値で件数を確認する。
                assert "mcp" in tool_text.lower(), actual_events
                tool_outputs = [
                    event.get("text") or event.get("step_update", {}).get("tool_info", {}).get("output", "")
                    for event in actual_events
                ]
                entry["mcp_results"] = [
                    output
                    for output in tool_outputs
                    if '"transport":"stdio"' in "".join(output.replace("\\", "").split())
                    and '"total_matches":2' in "".join(output.replace("\\", "").split())
                ]
                assert entry["mcp_results"], actual_events
                assert "P4_READ_ALPHA" in tool_text and "P4_READ_BETA" in tool_text
                assert text.splitlines()[1] in tool_text
                response_text = result["agent_message"].strip()
                if response_text.startswith("```json\n") and response_text.endswith("\n```"):
                    response_text = response_text[len("```json\n") : -len("\n```")]
                value = json.loads(response_text)
                assert value == {
                    "head": before["head"],
                    "git_status": "",
                    "rg_lines": ["1:P4_READ_ALPHA", "3:P4_READ_BETA"],
                    "python_text": text,
                    "sed_text": text,
                    "mcp_count": 2,
                }, value
                entry["after"] = _investigation_snapshot(repository)
                assert entry["after"] == before
        except BaseException as error:
            observed["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            observed["states_before_close"] = {
                key: {"engine": value.engine, "status": value.status, "turn_seq": value.turn_seq, "error": value.error}
                for key, value in manager.sessions.items()
            }
            await manager.close()
            observed["after_close"] = _investigation_snapshot(repository)
            observed["closed"] = True
            destination.write_text(json.dumps(observed, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"実backend受入記録: {destination}")


async def _wait_terminal(manager: server_manager.AgentsServerManager) -> dict[str, Any]:
    """待機上限へ達した応答は`running`を返すため、終了まで同じ待機を繰り返す。"""
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
    async with server_manager.AgentsServerManager() as manager:
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        cwd = str(pathlib.Path(__file__).parents[2])
        # 委譲先CLIが認証と設定を読み、PATHから起動できるよう、ホームと設定ディレクトリとPATHを戻す。
        isolation.restore_host_environment(monkeypatch)
        monkeypatch.setattr(
            _atk_config,
            "parse_unresolved_model_candidates",
            lambda _model_type, **_kwargs: [("claude", "sonnet[1m]", "medium")],
        )
        live_input = BACKGROUND_RESUME_INPUTS[mode]
        if mode == "delegate":
            await mcp_tools.start(cwd, mode="delegate", prompt=live_input.texts[0], model_type="chores_fast")
        else:
            await mcp_tools.start(cwd, mode="shell", command="sleep 2", summary_policy=live_input.texts[0])

        result = await manager.wait()

        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "AUTO_RESUME_COMPLETED"


@pytest.mark.asyncio
async def test_live_explore_reads_file_and_continues_same_session(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """探索起動が既知のファイルを読んで内容を返し、同じsessionへの継続でも別のファイルを読んで返す。"""
    async with server_manager.AgentsServerManager() as manager:
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
            lambda _model_type, **_kwargs: [("claude", "sonnet[1m]", "medium")],
        )
        start_text, continue_text = EXPLORE_READ_INPUT.texts
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

    # 孫sessionの終了による自動再開を確かめる入力。MCPの起動ツールと`atk agents wait`の実行を要求するため、
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
    """委譲先が孫sessionを起動して待機を表明すると、孫の終了後に同じsessionが手動の指示なしに再開し完了報告を返す。

    待機表明の行を完了報告として配送せず、再開したturnの結果だけを委譲元へ返すことを確かめる。
    """
    async with server_manager.AgentsServerManager() as manager:
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        cwd = str(pathlib.Path(__file__).parents[2])
        # 委譲先CLIが認証と設定を読み、PATHから起動できるよう、ホームと設定ディレクトリとPATHを戻す。
        isolation.restore_host_environment(monkeypatch)
        monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [candidate])
        started = await mcp_tools.start(cwd, mode="delegate", prompt=GRANDCHILD_INPUT.texts[0], model_type="execute")
        session = manager.sessions[started["session_id"]]

        result = await _wait_terminal(manager)

        assert result["status"] == "completed", result
        assert result["agent_message"].strip() == "AUTO_RESUME_COMPLETED", result
        assert "unobservedSessions" not in (result.get("error") or {}), result
        assert session.turn_seq == 2

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

    def __enter__(self) -> _PseudoResponsesApi:
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
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_CREDITS_PROVIDER", _PRIMARY_PROVIDER)
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_PAYG_PROVIDER", _FALLBACK_PROVIDER)
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_WORKER_CONNECTIONS", "credits,payg")
        monkeypatch.setattr(
            _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "gpt-5.4", "low")]
        )
        async with server_manager.AgentsServerManager() as manager:
            monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
            await mcp_tools.start(
                str(work),
                mode="delegate",
                prompt=f"{_SWITCH_TASK_MARKER}: シェルでコマンドを1回実行して結果を報告せよ。",
                model_type="chores_fast",
            )
            result = await manager.wait()
            # 待機上限へ達した応答は`running`を返すため、終了まで同じ待機を繰り返す。
            for _ in range(10):
                if result["status"] != "running":
                    break
                result = await manager.wait()
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
