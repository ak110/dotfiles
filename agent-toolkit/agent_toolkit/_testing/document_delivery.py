"""合成した分岐試験で、Codexの外側配送と後続Preを明示的に再現する。

実ホストの出力契約は`../_hooks/pretooluse/read_delivery_host_samples_test.py`の未改変標本で検収する。
この補助は既存の主体・宣言・起動用途の組合せ試験だけに使う。
"""

import hashlib
import json
import pathlib
import shutil
import subprocess
from collections.abc import Iterator

from agent_toolkit._atk import orchestrator
from agent_toolkit._common import state_paths
from agent_toolkit._hooks import notice, rules_context
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.pretooluse import agent_checks, dispatch, read_delivery
from agent_toolkit._testing.isolation import build_host_environ
from agent_toolkit._testing.pretooluse_support import codex_delivered_payload
from agent_toolkit._testing.pretooluse_support import run_hook_with_notice_details as _run_hook_in_process

# 公開hookは実Gitを起動するたびに子孫を探索し、Windowsではプロセスごとに全プロセスの一覧から親PIDを引く。
# 所有CPUがホストのプロセス数に応じて増えるため、Windowsでは計測値に余裕を持たせた個別上限を使う。
WINDOWS_HOOK_CPU_REASON = (
    "公開hookの実Git起動後の子孫探索は、Windowsでホストのプロセス数に応じて所有CPUが増える。"
    "上限はstheno（約590プロセス）の5回計測の最大に約2倍の余裕を加えた値"
)


def hook_text(stdout: str) -> str:
    """公開hookの標準出力JSONを解析し、含まれる文字列値を改行でつないで返す。

    JSON文字列ではWindowsのパス区切りがエスケープされるため、通知が示す資料のパスは解析後の文字列から探す。
    """

    def strings(value: object) -> Iterator[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    return "\n".join(strings(json.loads(stdout))) if stdout.strip() else ""


def run_bash(command: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """ホストのPOSIX shellでコマンドを実行し、文字列の結果を返す。

    WindowsのPATHでは`bash`が未導入WSLの起動用実行体へ解決されることがあるため、
    Codexのセッションと同じGit Bashのshimを先頭に置いた環境から解決する。
    """
    environment = orchestrator.session_env(build_host_environ(), "codex")
    bash = shutil.which("bash", path=environment["PATH"])
    assert bash is not None, environment["PATH"]
    return subprocess.run(
        [bash, "-c", command],
        cwd=cwd,
        env=environment,
        # Bashツールと同じく標準入力を渡さず、標準入力を読むコマンド（`jq 'input'`など）を待たせない。
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        # `cut -c`はバイト単位で抽出するため、マルチバイト文字の一部だけを含む出力を置換文字で受け取る。
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def prepare_state(monkeypatch, read, update) -> None:
    """呼出側の代用関数の引数契約を保ち、読込通知の状態差替えを共通化する。"""
    for module in (agent_checks, read_delivery, rules_context, notice, dispatch):
        monkeypatch.setattr(module, "read_state", read, raising=False)
        monkeypatch.setattr(module, "update_state", update)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    notice.set_warning_session_id("")


def _payload(value: dict) -> dict:
    actor = value.get("agent_id", "main")
    identity = json.dumps([value.get("session_id", "test"), actor]).encode("utf-8")
    directory = state_paths.state_dir() / "test-deliveries"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(identity).hexdigest() + ".jsonl")
    if actor != "main" and value.get("agent_transcript_path"):
        path = pathlib.Path(value["agent_transcript_path"])
    path.touch(exist_ok=True)
    return {"transcript_path": str(path), **value, **({"agent_transcript_path": str(path)} if actor != "main" else {})}


def run_hook(subcommand: str, payload: dict, env_overrides: dict[str, str] | None = None):
    """合成出力を外側へ配送した後、次の公開Preを経てから試験へ制御を返す。"""
    if not is_codex_payload(payload):
        return _run_hook_in_process(subcommand, payload, env_overrides)
    value = _payload(payload)
    result = _run_hook_in_process(subcommand, value, env_overrides)
    if subcommand == "posttooluse" and value.get("tool_name") in {"Read", "Bash"}:
        value = codex_delivered_payload(value)
        next_result = _run_hook_in_process(
            "pretooluse",
            {**value, "hook_event_name": "PreToolUse", "tool_name": "ObserveDelivery", "tool_input": {}},
            env_overrides,
        )
        assert next_result.returncode == 0 and not next_result.stderr
    return result


def observe_received(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> None:
    """直接観測を使う既存試験にも、外側配送と後続の取得判定を追加する。"""
    read_delivery.observe(payload, tool_name, tool_input, session_id)
    if is_codex_payload(payload):
        value = codex_delivered_payload(
            _payload({"session_id": session_id, **payload, "tool_name": tool_name, "tool_input": tool_input})
        )
        read_delivery.deliver(value, "ObserveDelivery", {}, session_id)
