#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code Stop フック: dotfiles 個人環境専用 pyfltr 振り返りプロンプト。

pyfltr を使用したセッションの終了時に、pyfltr の動作に関する振り返りを促す。
対象はメインの transcript のみ（サブエージェント履歴は別ファイルのため対象外）。

動作フロー:
1. stdin JSON から session_id・transcript_path を取得する
2. 状態ファイル `${TMPDIR}/claude-dotfiles-stop-{session_id}.json` を読み、
   `pyfltr_advice_given == true` なら即 approve で終了する
3. transcript_path が空または不正な場合は approve で終了する
4. transcript 内の assistant エントリの tool_use ブロックで `name == "Bash"` かつ
   `input.command` に `pyfltr` トークン一致があるか走査する
5. 一致なしなら approve で終了する
6. `_stop_gate.is_real_session_end(transcript_path)` が False なら approve で終了する
7. 状態ファイルに `pyfltr_advice_given = true` を書き込む
8. block を出力してリフレクションプロンプトを返す

exit code: 常に 0。
stdout に JSON (decision: approve | block) を出力する。
例外・想定外入力時は approve にフォールバックする。
"""

import contextlib
import json
import pathlib
import re
import sys
import tempfile
import traceback

# agent-toolkit の共通ゲートモジュールを import する。
# plugin が無効化されていても dotfiles リポジトリ上にファイルが存在し続けるため import は成立する。
sys.path.insert(0, str(pathlib.Path.home() / "dotfiles" / "plugins" / "agent-toolkit" / "scripts"))
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_real_session_end,  # type: ignore[import]
)

# `\bpyfltr\b` に相当する正規表現。
# uv run pyfltr / pyfltr / uv run --script ... pyfltr など典型的な呼び出し形式を網羅する。
_PYFLTR_PATTERN = re.compile(r"\bpyfltr\b")

# LLM 宛てメッセージの共通プレフィックス / サフィックス。
_MESSAGE_PREFIX = "[auto-generated: dotfiles/claude_hook_stop]"
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


def _llm_notice(body: str) -> str:
    """LLM 宛てメッセージを標準プレフィックス / サフィックス付きで整形する。"""
    return f"{_MESSAGE_PREFIX} {body} {_MESSAGE_SUFFIX}"


def _state_path(session_id: str) -> pathlib.Path:
    """セッション状態ファイルのパスを返す。

    plugin 側の `claude-agent-toolkit-{session_id}.json` と分離して責務境界を明確にする。
    tempdir を使う理由: セッション状態は揮発で構わず、OS 再起動時に自動消去されるため。
    """
    return pathlib.Path(tempfile.gettempdir()) / f"claude-dotfiles-stop-{session_id}.json"


def _read_state(path: pathlib.Path) -> dict:
    """状態ファイルを読み込む。ファイル未作成・破損時は空 dict を返す。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_state(path: pathlib.Path, state: dict) -> None:
    """状態ファイルを書き込む。書き込み失敗は無視する。"""
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _has_pyfltr_usage(transcript_path: str) -> bool:
    r"""Transcript 内に pyfltr を Bash 経由で実行した痕跡があるか確認する。

    assistant エントリの message.content 内の tool_use ブロックのうち
    `name == "Bash"` かつ `input.command` に `\bpyfltr\b` が含まれるものを検索する。
    サブエージェント（isSidechain）は別ファイルのため対象外。
    """
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    for line in lines:
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use" or block.get("name") != "Bash":
                continue
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                continue
            command = tool_input.get("command", "")
            if isinstance(command, str) and _PYFLTR_PATTERN.search(command):
                return True
    return False


def _approve() -> None:
    print(json.dumps({"decision": "approve"}, ensure_ascii=False))


def _block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def _main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        _approve()
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        _approve()
        return 0

    state_file = _state_path(session_id)
    state = _read_state(state_file)

    # セッション 1 回制限: 既に発火済みなら即 approve する。
    if state.get("pyfltr_advice_given", False):
        _approve()
        return 0

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if not transcript_path:
        _approve()
        return 0

    # pyfltr の使用有無を確認する。
    if not _has_pyfltr_usage(transcript_path):
        _approve()
        return 0

    # 真のセッション終了かどうかを共通ゲートで確認する。
    if not is_real_session_end(transcript_path):
        _approve()
        return 0

    # 発火: block 前に pyfltr_advice_given を記録する。
    state["pyfltr_advice_given"] = True
    _write_state(state_file, state)

    _block(
        _llm_notice(
            "pyfltrセッションレビュー: 今回のセッションでpyfltrを使用した。"
            "終了前に以下を振り返って報告すること。"
            "(1) pyfltrの動作・出力に違和感や分かりにくさはなかったか "
            "(2) 挙動・メッセージ・ドキュメント（`plugins/agent-toolkit/skills/pyfltr-usage/SKILL.md`含む）"
            "に改善提案はあるか。改善点がない場合は「特になし」と明示してよい。"
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
