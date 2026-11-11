"""待機CLI試験の環境と結果・上限の補助。"""

import json
import os
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, shared_layout, shared_roots
from agent_toolkit._common import state_paths, wait_schedule


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """結果の無い待機を即時に返して公開引数へ上限を露出させない。"""
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)


@pytest.fixture(name="wait_environment")
def _wait_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """状態ファイルの解決先をテスト用ディレクトリへ隔離する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_STATUS_HOST_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    return shared_layout.results_directory("root-session", tmp_path)


def _running_body(response: dict[str, Any]) -> dict[str, Any]:
    """待機を続ける応答から、`atk agents wait`の再実行を示す次の操作を確かめて除いた本体を返す。

    終了時の結果の行は次の操作を持たない場合があるため、そのまま返す。
    """
    if response.get("status") != "running":
        return response
    body = dict(response)
    assert "atk agents wait" in body.pop("next_action")
    return body


def _write_own_status(results_directory: pathlib.Path, sessions: list[object]) -> pathlib.Path:
    """自身の書込主体の状態ファイルを作成する。"""
    root_status = results_directory.parent / "root.json"
    root_status.parent.mkdir(parents=True, exist_ok=True)
    root_status.write_text(json.dumps({"version": 1, "sessions": sessions}), encoding="utf-8")
    return root_status


def _publish_result_on_sleep(results_directory: pathlib.Path) -> Callable[[float], None]:
    """sleepの代わりに`session-1`の終了時の結果を公開する関数を返す。"""

    def publish_result(_seconds: float) -> None:
        results_directory.mkdir(parents=True, exist_ok=True)
        (results_directory / "session-1.json").write_text(
            json.dumps({"status": "completed"}),
            encoding="utf-8",
        )

    return publish_result


def _wait_for_session_1_result(results_directory: pathlib.Path) -> int:
    """テスト用のroot sessionから`session-1`の結果を待つ。"""
    return agents_wait.wait_for_result(
        environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
        state_root=results_directory.parents[2],
    )


_CODEX_ROOT = "mcp-0123456789abcdef0123456789abcdef"


def _codex_top_level_state(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """ownerを持たないCodex最上位の会話と、そのMCPが書いたプロセス専用ルート・別会話のルートを用意する。"""
    for name in ("AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "AGENT_TOOLKIT_DELEGATED_SESSION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    for root_session_id, remote_session_id in ((_CODEX_ROOT, "codex-child"), ("claude-root", "claude-child")):
        directory = shared_layout.status_directory(root_session_id, tmp_path)
        directory.mkdir(parents=True)
        (directory / "root.json").write_text(
            json.dumps({"version": 1, "sessions": [{"session_id": remote_session_id, "status": "running"}]}),
            encoding="utf-8",
        )
    shared_roots.write_root_alias("claude-session", "claude-root", tmp_path)
    results = shared_layout.results_directory(_CODEX_ROOT, tmp_path)
    results.mkdir(parents=True)
    return results


_TTL_CASES = [
    pytest.param({"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}, 1740.0, 1740.0, id="claude-code-1h"),
    pytest.param({"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "5m"}, 270.0, 270.0, id="claude-code-5m"),
    pytest.param({"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}, 270.0, 270.0, id="no-claudecode"),
    # CLIはMCPのツール呼び出しを経由しないため、委譲先の印だけでは縮めない。MCPの待機だけが240秒へ縮める。
    pytest.param(
        {"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "AGENT_TOOLKIT_DELEGATED_SESSION": "1"},
        1740.0,
        240.0,
        id="delegated",
    ),
]


def _set_ttl_environment(monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> None:
    """保持期間とホストの判定入力を指定の条件だけへ固定し、差し替えた上限の導出を実装の`get_cli_wait_timeout`へ戻す。"""
    for name in ("CLAUDECODE", "FORCE_PROMPT_CACHING_5M", "ENABLE_PROMPT_CACHING_1H", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", wait_schedule.get_cli_wait_timeout)


def _observe_cli_wait_limit(
    monkeypatch: pytest.MonkeyPatch,
    results_directory: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    probe_limit: float,
) -> float:
    """終了しない委譲先を`atk agents wait`で待ち、終了コード3で戻った時点の経過秒数を返す。

    最初の待機で`probe_limit`の0.5秒前まで時刻を進め、以降は待機した秒数だけ進める。
    実際の上限が`probe_limit`と一致する場合だけ、残り0.5秒の待機を経て同じ時刻に上限へ到達する。
    """
    identity = shared_roots.resolve_status_file_identity(dict(os.environ))
    assert identity is not None
    status_path = results_directory.parent / identity.file_name
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps({"version": 1, "sessions": [{"session_id": "session-1"}]}), encoding="utf-8")
    clock = [0.0]
    sleeps: list[float] = []

    def advance(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] = probe_limit - 0.5 if len(sleeps) == 1 else clock[0] + seconds

    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(agents_wait.time, "sleep", advance)

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    assert sleeps == [1.0, 0.5]
    captured = capsys.readouterr()
    assert not captured.err
    output = captured.out
    if output.startswith("保存先: "):
        output = pathlib.Path(output.splitlines()[0].removeprefix("保存先: ")).read_text(encoding="utf-8")
    timed_out = json.loads(output)
    # 待機上限で終わった行は、待機を続ける操作を次の操作として持つ。
    assert "atk agents wait" in timed_out.pop("next_action")
    assert timed_out == {"session_id": "session-1", "status": "running"}
    return clock[0]


_STOPPED_SERVER = "20261019T010203000000Z-777-abcdef01"
