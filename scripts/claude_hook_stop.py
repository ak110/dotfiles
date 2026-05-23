#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code Stopフック: dotfiles個人環境専用セッション振り返りプロンプト。

pyfltrまたはagent-toolkitスキルを使用したセッションの終了時に、
個人環境向け拡張章を担う`session-review-dotfiles`スキルの追加呼び出しを誘導する。
対象はメインのtranscriptのみ（サブエージェント履歴は別ファイルのため対象外）。

本hookはdotfiles個人環境側の2カ所同期対象の1つで、Stopイベントで並列発火する
配布物hook（`agent-toolkit/scripts/stop_advisor.py`）と責務を分離している。

- `agent-toolkit/scripts/stop_advisor.py` — 配布物。`agent-toolkit:session-review`スキルの
  呼び出し誘導を担う（プロジェクトドキュメント章を対象とする標準フロー）
- 本hook（`scripts/claude_hook_stop.py`） — dotfiles個人環境専用。
  pyfltrまたはagent-toolkitスキル使用検出時に`session-review-dotfiles`スキルの
  追加呼び出しを誘導する（pyfltr・agent-toolkitの2拡張章を追加するため）
- `.chezmoi-source/dot_claude/skills/session-review-dotfiles/SKILL.md` —
  ユーザー手動起動または本hookからの呼び出しで動作。dotfiles拡張章を担う

本hookと`session-review-dotfiles/SKILL.md`の2カ所は同期対象。

LLM宛て出力は`agent-toolkit/scripts/_message_format.llm_notice`経由で整形する。
プレフィックス／サフィックス規約と出力先フィールド（`reason`・`additionalContext`）の詳細は
`_message_format`モジュールのdocstringを参照する。
参照経路は`Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"`を
`sys.path`に追加して解決する。プラグイン無効化時もファイル自体は存在しimportは成立する。
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


def _llm_notice(body: str) -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス / サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID)


def _state_path(session_id: str) -> pathlib.Path:
    """セッション状態ファイルのパスを返す。

    plugin 側の `claude-agent-toolkit-{session_id}.json` と分離して責務境界を明確にする。
    tempdir を使う理由はセッション状態が揮発で構わず、OS 再起動時に自動消去されるためである。
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

    has_pyfltr = _has_pyfltr_usage(transcript_path)
    has_agent_toolkit = _has_agent_toolkit_usage(transcript_path)

    if not has_pyfltr and not has_agent_toolkit:
        _approve()
        return 0

    if not is_real_session_end(transcript_path):
        _approve()
        return 0

    # 発火: block 前に advice_given を記録する。
    state["advice_given"] = True
    _write_state(state_file, state)

    # 振り返り手順全体は `agent-toolkit:session-review` スキルが保持し、その呼び出し誘導は
    # 同時発火する `stop_advisor.py` が担う。本 hook は dotfiles 拡張章を担う
    # `session-review-dotfiles` スキルの追加呼び出しのみを誘導する責務分離設計を採る。
    body = (
        "session-review handoff (dotfiles extension): in addition to the"
        " `agent-toolkit:session-review` Skill invoked via the `[auto-generated: agent-toolkit/stop_advisor]`"
        " notice, also invoke the `session-review-dotfiles` Skill via the Skill tool."
        " The dotfiles skill adds pyfltr and agent-toolkit improvement sections on top of the"
        " project documentation section covered by `agent-toolkit:session-review`."
    )
    _block(_llm_notice(body))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
