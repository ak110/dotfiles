#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code Stop フック: dotfiles 個人環境専用セッション振り返りプロンプト。

pyfltr または agent-toolkit スキルを使用したセッションの終了時に、
それぞれの動作に関する振り返りを促す。
対象はメインの transcript のみ（サブエージェント履歴は別ファイルのため対象外）。

配布物 hook (`agent-toolkit/scripts/stop_advisor.py`) と同じ Stop イベントで並列発火する前提で書く。
振り返りメッセージ全体に適用される共通指示
（自己完結性・行フォーマット・空時の「指摘無し」・出力スタイル）は配布物 hook の reason が出力するため、
本 hook では当該章固有の指示と、cwd に応じた統合判定
（dotfiles プロジェクト中なら agent-toolkit 章を「## プロジェクトドキュメント改善提案」へ統合し、
pyfltr プロジェクト中なら pyfltr 章を同じく統合する）のみを記述する。

動作フロー:
1. stdin JSON から session_id・transcript_path・cwd を取得する
2. 状態ファイル `${TMPDIR}/claude-dotfiles-stop-{session_id}.json` を読み、
   `advice_given == true` なら即 approve で終了する
3. transcript_path が空または不正な場合は approve で終了する
4. transcript 内の assistant エントリの tool_use ブロックを走査し、
   pyfltr 使用（Bash ツールで `\bpyfltr\b` を含むコマンド）と
   agent-toolkit 使用（Skill ツールで `agent-toolkit:` を含むスキル名）をそれぞれ確認する
5. 両方一致なしなら approve で終了する
6. `_stop_gate.is_real_session_end(transcript_path)` が False なら approve で終了する
7. 状態ファイルに `advice_given = true` を書き込む
8. cwd から現プロジェクトを判定し、章ごとに統合 or 別建ての指示を生成する
9. block を出力して振り返りプロンプトを返す

exit code: 常に 0。
stdout に JSON (decision: approve | block) を出力する。
例外・想定外入力時は approve にフォールバックする。
"""

import contextlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import traceback

# agent-toolkit の共通ゲートモジュールを import する。
# plugin が無効化されていても dotfiles リポジトリ上にファイルが存在し続けるため import は成立する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_real_session_end,  # type: ignore[import]
)

# `\bpyfltr\b` に相当する正規表現。
# uv run pyfltr / pyfltr / uv run --script ... pyfltr など典型的な呼び出し形式を網羅する。
_PYFLTR_PATTERN = re.compile(r"\bpyfltr\b")

# agent-toolkit スキル呼び出しを検出する正規表現。
# Skill ツールの input.skill フィールドに `agent-toolkit:` が含まれるケースを対象とする。
_AGENT_TOOLKIT_PATTERN = re.compile(r"\bagent-toolkit:")

# このスクリプトの hook 識別子。
_HOOK_ID = "dotfiles/claude_hook_stop"

# 章統合の対象となるプロジェクト名（git rev-parse --show-toplevel の basename）。
# 個人環境専用 hook のため運用上のリポジトリ名をハードコードしてよい。
_INTEGRATABLE_PROJECTS = frozenset({"dotfiles", "pyfltr"})


def _llm_notice(body: str) -> str:
    """LLM 宛てメッセージを標準プレフィックス / サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID)


def _detect_project(cwd: str) -> str | None:
    """Cwd から現在のプロジェクト（リポジトリ名）を返す。

    git 管理外・対象外プロジェクト・コマンド失敗時は None を返す。
    SKILL.md の統合ルール
    （pyfltr プロジェクト中なら pyfltr 章を、dotfiles プロジェクト中なら
    agent-toolkit 章を、それぞれプロジェクトドキュメント章へ統合する）
    の判定に使う。
    """
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    name = pathlib.Path(result.stdout.strip()).name
    return name if name in _INTEGRATABLE_PROJECTS else None


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

    cwd = payload.get("cwd", "")
    project = _detect_project(cwd) if isinstance(cwd, str) else None

    # 共通指示（自己完結性・行フォーマット・空時の「指摘無し」・出力スタイル）は
    # 同時発火する `agent-toolkit/scripts/stop_advisor.py` の reason が出力するため、
    # 本 hook では当該章固有の指示のみを記述する。
    sections = []
    if has_pyfltr:
        if project == "pyfltr":
            sections.append(
                "pyfltr behavior/message issues: append items under the existing"
                " '## プロジェクトドキュメント改善提案' heading"
                " (do not create a separate '## pyfltr改善提案' heading)."
            )
        else:
            sections.append(
                "pyfltr review: list pyfltr behavior/message issues in Japanese under the heading '## pyfltr改善提案'."
            )
    if has_agent_toolkit:
        if project == "dotfiles":
            sections.append(
                "agent-toolkit issues (skills/agents/hooks under `agent-toolkit/`,"
                " including `skills/pyfltr-usage/SKILL.md`,"
                " and rules under `~/.claude/rules/agent-toolkit/`):"
                " append items under the existing"
                " '## プロジェクトドキュメント改善提案' heading"
                " (do not create a separate '## agent-toolkit改善提案' heading)."
            )
        else:
            sections.append(
                "agent-toolkit review: list issues in Japanese for the agent-toolkit plugin"
                " (`agent-toolkit/`, including `skills/pyfltr-usage/SKILL.md`)"
                " and `~/.claude/rules/agent-toolkit/` rules"
                " under the heading '## agent-toolkit改善提案'."
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
