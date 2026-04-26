#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code Stop フック: dotfiles 個人環境専用セッション振り返りプロンプト。

pyfltr または agent-toolkit スキルを使用したセッションの終了時に、
それぞれの動作に関する振り返りを促す。
対象はメインの transcript のみ（サブエージェント履歴は別ファイルのため対象外）。

動作フロー:
1. stdin JSON から session_id・transcript_path を取得する
2. 状態ファイル `${TMPDIR}/claude-dotfiles-stop-{session_id}.json` を読み、
   `advice_given == true` なら即 approve で終了する
3. transcript_path が空または不正な場合は approve で終了する
4. transcript 内の assistant エントリの tool_use ブロックを走査し、
   pyfltr 使用（Bash ツールで `\bpyfltr\b` を含むコマンド）と
   agent-toolkit 使用（Skill ツールで `agent-toolkit:` を含むスキル名）をそれぞれ確認する
5. 両方一致なしなら approve で終了する
6. `_stop_gate.is_real_session_end(transcript_path)` が False なら approve で終了する
7. 状態ファイルに `advice_given = true` を書き込む
8. block を出力して振り返りプロンプトを返す

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
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "plugins" / "agent-toolkit" / "scripts"),
)
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_real_session_end,  # type: ignore[import]
)

# `\bpyfltr\b` に相当する正規表現。
# uv run pyfltr / pyfltr / uv run --script ... pyfltr など典型的な呼び出し形式を網羅する。
_PYFLTR_PATTERN = re.compile(r"\bpyfltr\b")

# agent-toolkit スキル呼び出しを検出する正規表現。
# Skill ツールの input.skill フィールドに `agent-toolkit:` が含まれるケースを対象とする。
_AGENT_TOOLKIT_PATTERN = re.compile(r"\bagent-toolkit:")

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


def _iter_tool_use_blocks(transcript_path: str):
    """Transcript 内のメイン assistant エントリから tool_use ブロックを yield する。

    サブエージェント（isSidechain）は別ファイルのため対象外。
    """
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return
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
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield block


def _has_pyfltr_usage(transcript_path: str) -> bool:
    r"""Transcript 内に pyfltr を Bash 経由で実行した痕跡があるか確認する。

    tool_use ブロックのうち `name == "Bash"` かつ `input.command` に
    `\bpyfltr\b` が含まれるものを検索する。
    """
    for block in _iter_tool_use_blocks(transcript_path):
        if block.get("name") != "Bash":
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        command = tool_input.get("command", "")
        if isinstance(command, str) and _PYFLTR_PATTERN.search(command):
            return True
    return False


def _has_agent_toolkit_usage(transcript_path: str) -> bool:
    """Transcript 内に agent-toolkit スキルを呼び出した痕跡があるか確認する。

    tool_use ブロックのうち `name == "Skill"` かつ `input.skill` に
    `agent-toolkit:` が含まれるものを検索する。
    """
    for block in _iter_tool_use_blocks(transcript_path):
        if block.get("name") != "Skill":
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        skill = tool_input.get("skill", "")
        if isinstance(skill, str) and _AGENT_TOOLKIT_PATTERN.search(skill):
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
    if state.get("advice_given", False):
        _approve()
        return 0

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if not transcript_path:
        _approve()
        return 0

    # 各ツールの使用有無を確認する。
    has_pyfltr = _has_pyfltr_usage(transcript_path)
    has_agent_toolkit = _has_agent_toolkit_usage(transcript_path)

    if not has_pyfltr and not has_agent_toolkit:
        _approve()
        return 0

    # 真のセッション終了かどうかを共通ゲートで確認する。
    if not is_real_session_end(transcript_path):
        _approve()
        return 0

    # 発火: block 前に advice_given を記録する。
    state["advice_given"] = True
    _write_state(state_file, state)

    sections = []
    if has_pyfltr:
        sections.append(
            "pyfltr session review: pyfltr was used in this session. "
            "Before closing, reflect on and report the following in Japanese. "
            "(1) Were there any confusing or unclear aspects in pyfltr's behavior or output "
            "(misleading messages, unexpected exit codes, hard-to-read diagnostics)? "
            "(2) Are there missing entries or incorrect content in the existing documentation "
            "that future sessions would benefit from clarifying? "
            "Suggestion targets (the user will apply changes separately, so stop at the proposal stage): "
            "pyfltr's own behavior and messages, and "
            "`plugins/agent-toolkit/skills/pyfltr-usage/SKILL.md` (the usage reference). "
            "If there are no improvements to suggest, explicitly state '指摘無し'."
        )
    if has_agent_toolkit:
        sections.append(
            "agent-toolkit session review: agent-toolkit skills were used in this session. "
            "Before closing, reflect on and report the following in Japanese. "
            "(1) Were there violations of agent.md procedures, "
            "such as the bug-fix root-cause investigation or the verify-then-commit order? "
            "(2) Were any plugin or rule instructions confusing or unclear? "
            "(3) Are there missing entries or incorrect content in the existing instructions? "
            "Suggestion targets (the user will apply changes separately, so stop at the proposal stage): "
            "the agent-toolkit plugin (skills, hooks, subagents under `plugins/agent-toolkit/`) "
            "and the rules under `~/.claude/rules/agent-toolkit/`. "
            "If there are no improvements to suggest, explicitly state '指摘無し'."
        )
    _block(_llm_notice(" | ".join(sections)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
