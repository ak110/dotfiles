"""SessionStartの旧Codex User scope MCP診断を検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook.py"
_MODULE_PATH = pathlib.Path(__file__).resolve().parent / "session_start.py"


def _run(payload: dict | str, home: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """共通hook入口からSessionStartを実行する。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env["HOME"] = str(home)
    return _fork_runner.run_script(_SCRIPT, argv=("session_start",), input=text, env=env)


def _write_config(home: pathlib.Path, value: object) -> pathlib.Path:
    """テスト用のClaude Code User scope設定を書き込む。"""
    home.mkdir(parents=True, exist_ok=True)
    path = home / ".claude.json"
    path.write_text(json.dumps({"mcpServers": {"codex": value}}, ensure_ascii=False), encoding="utf-8")
    return path


def test_session_start_diagnoses_legacy_definition_without_writing(tmp_path: pathlib.Path) -> None:
    """旧登録を検知して手動削除手順を返し、設定ファイルを変更しない。"""
    home = tmp_path / "home"
    path = _write_config(home, {"command": "codex", "args": ["mcp-server"], "timeout": 7_200_000})
    before = path.read_bytes()

    result = _run({"source": "startup"}, home)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "claude mcp get codex" in context
    assert "claude mcp remove --scope user codex" in context
    assert path.read_bytes() == before


def test_session_start_diagnoses_legacy_definition_with_custom_fields(tmp_path: pathlib.Path) -> None:
    """利用者フィールドを含む旧登録も削除せず確認手順だけを案内する。"""
    home = tmp_path / "home"
    _write_config(
        home,
        {
            "type": "stdio",
            "command": "codex",
            "args": ["mcp-server"],
            "timeout": 7_200_000,
            "env": {"CUSTOM": "value"},
        },
    )

    result = _run({"source": "resume"}, home)

    assert result.returncode == 0
    assert "旧User scope MCP" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    "value",
    [
        {"command": "other", "args": ["mcp-server"]},
        {"command": "codex", "args": ["mcp-server"], "timeout": 1_000},
        {"type": "http", "command": "codex", "args": ["mcp-server"]},
    ],
)
def test_session_start_ignores_non_legacy_definition(tmp_path: pathlib.Path, value: dict[str, object]) -> None:
    """旧登録と一致しない利用者設定を変更対象・診断対象にしない。"""
    home = tmp_path / "home"
    _write_config(home, value)

    result = _run({"source": "startup"}, home)

    assert result.returncode == 0
    assert not result.stdout


def test_session_start_ignores_invalid_payload_and_missing_config(tmp_path: pathlib.Path) -> None:
    """不正入力や設定不在ではフックをfail-openする。"""
    home = tmp_path / "home"

    assert _run("not json", home).returncode == 0
    result = _run({"source": "startup"}, home)
    assert result.returncode == 0
    assert not result.stdout


def test_session_start_script_is_registered_in_dispatcher() -> None:
    """共通フック入口からSessionStartサブコマンドを解決できる。"""
    assert _MODULE_PATH.is_file()
