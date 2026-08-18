"""scripts/claude_hook_stop_bell.py のテスト。

応答終了で入力待ちになった場合だけ端末ベルを鳴らすStopフックのテスト。独立スクリプトなので
fork-server経由（フォールバック時はsubprocess）で起動しstdout（JSON）を検証する。
判定分岐は自律セッション・非同期待機中・通常終了・入力不正を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

# 共通テストヘルパー読み込みのため agent-toolkit/scripts/ を sys.path へ追加する。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent-toolkit" / "scripts"))
import _fork_runner  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook.py"

_ENV_PROCESS_LOOP = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_LEGACY_ENV_PROCESS_LOOP = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"

_BELL = "\a"


def _write_transcript(tmp_path: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    return transcript


def _user_entry(text: str = "hello") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant_entry(*, async_tool: str | None = None) -> dict:
    content: list[dict[str, object]] = [{"type": "text", "text": "対応した。"}]
    if async_tool is not None:
        content.append({"type": "tool_use", "id": "x", "name": async_tool, "input": {}})
    message: dict[str, object] = {"role": "assistant", "content": content, "stop_reason": "end_turn"}
    return {"type": "assistant", "message": message}


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path,
    process_loop_env: str | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    env.pop(_ENV_PROCESS_LOOP, None)
    env.pop(_LEGACY_ENV_PROCESS_LOOP, None)
    if process_loop_env is not None:
        env[process_loop_env] = "1"
    return _fork_runner.run_script(_SCRIPT, argv=("stop_bell",), input=text, env=env)


def _output(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


class TestRingCondition:
    """ベルを鳴らす条件: 対話セッションで背景の稼働が無い状態の応答終了。"""

    def test_interactive_turn_end_rings(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_entry()])
        result = _run(
            {"session_id": "interactive", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert _output(result).get("terminalSequence") == _BELL

    def test_missing_transcript_rings(self, tmp_path: pathlib.Path):
        """transcript未指定でも背景稼働を判定できないだけで、入力待ちとしてベルを鳴らす。"""
        result = _run({"session_id": "no-transcript"}, state_dir=tmp_path)
        assert _output(result).get("terminalSequence") == _BELL


class TestSilentConditions:
    """ベルを鳴らさない条件: 自律セッション・背景稼働中・判定材料の欠落。"""

    def test_process_loop_session_is_silent(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_entry()])
        result = _run(
            {"session_id": "autonomous", "transcript_path": str(transcript)},
            state_dir=tmp_path,
            process_loop_env=_ENV_PROCESS_LOOP,
        )
        assert "terminalSequence" not in _output(result)

    def test_legacy_process_loop_env_is_silent(self, tmp_path: pathlib.Path):
        """旧process-loopの移行互換名だけが設定された場合も鳴らさない。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_entry()])
        result = _run(
            {"session_id": "legacy-autonomous", "transcript_path": str(transcript)},
            state_dir=tmp_path,
            process_loop_env=_LEGACY_ENV_PROCESS_LOOP,
        )
        assert "terminalSequence" not in _output(result)

    def test_pending_async_work_is_silent(self, tmp_path: pathlib.Path):
        """直前ターンの最後のtool_useが非同期待機系 → 待機の継続のため鳴らさない。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_entry(async_tool="Agent")])
        result = _run(
            {"session_id": "pending-async", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert "terminalSequence" not in _output(result)

    def test_invalid_json_is_silent(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0
        assert "terminalSequence" not in _output(result)

    def test_empty_session_id_is_silent(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "", "transcript_path": "/x"}, state_dir=tmp_path)
        assert "terminalSequence" not in _output(result)
