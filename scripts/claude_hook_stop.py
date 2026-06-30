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

配布物Stopフック（`stop_advisor.py`）との誘導重複を避けるため、個人フックPostToolUse
（`claude_hook_posttooluse.py`）が`agent-toolkit:*`スキルまたは`session-review-dotfiles`スキル
使用を検出した際に`session_review_extension_pending`フラグを立て、配布物Stopフック側が
同フラグを参照して自身の誘導を抑制する。

`stop_hook_active`が真の場合は構造判定・誘導生成を行わず無条件approveとする。
誘導文の先頭には`agent-toolkit/scripts/_message_format.SESSION_REVIEW_PRECHECK`を付与し、
質問直後等の終了相当ケースでスキル起動を抑止する。両者の設計詳細は
`agent-toolkit/scripts/stop_advisor.py`のモジュールdocstringを参照する。

LLM宛て出力は`agent-toolkit/scripts/_message_format.llm_notice`経由で整形し、
`decision: "block"`＋`reason`フィールドへ載せて返す。
プレフィックス／サフィックス規約と出力先フィールドの詳細は
`_message_format`モジュールのdocstringを参照する。
参照経路は`Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"`を
`sys.path`に追加して解決する。プラグイン無効化時もファイル自体は存在しimportは成立する。
"""

import collections.abc
import json
import pathlib
import re
import sys
import traceback

# agent-toolkit の共通ゲートモジュールを import する。
# plugin が無効化されていても dotfiles リポジトリ上にファイルが存在し続けるため import は成立する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _message_format import SESSION_REVIEW_PRECHECK  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import is_pending_async_work  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# `\bpyfltr\b` に相当する正規表現。
# uv run pyfltr / pyfltr / uv run --script ... pyfltr など典型的な呼び出し形式を網羅する。
_PYFLTR_PATTERN = re.compile(r"\bpyfltr\b")

# agent-toolkit スキル呼び出しを検出する正規表現。
# Skill ツールの input.skill フィールドに `agent-toolkit:` が含まれるケースを対象とする。
_AGENT_TOOLKIT_PATTERN = re.compile(r"\bagent-toolkit:")

# このスクリプトの hook 識別子。
_HOOK_ID = "dotfiles/claude_hook_stop"

# 拡張章スキル名。本hookと同期対象のSKILL.md側でも参照される。
_EXTENSION_SKILL = "session-review-dotfiles"


def _llm_notice(body: str) -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス / サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID)


def _iter_tool_use_blocks(transcript_path: str) -> collections.abc.Iterator[dict]:
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


def _has_tool_usage(
    transcript_path: str,
    tool_name: str,
    field_name: str,
    pattern: re.Pattern[str],
) -> bool:
    """Transcript内に指定ツールの呼び出し痕跡があるか確認する。"""
    for block in _iter_tool_use_blocks(transcript_path):
        if block.get("name") != tool_name:
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        value = tool_input.get(field_name, "")
        if isinstance(value, str) and pattern.search(value):
            return True
    return False


def _has_pyfltr_usage(transcript_path: str) -> bool:
    """Transcript内にpyfltrをBash経由で実行した痕跡があるか確認する。"""
    return _has_tool_usage(transcript_path, "Bash", "command", _PYFLTR_PATTERN)


def _has_agent_toolkit_usage(transcript_path: str) -> bool:
    """Transcript内にagent-toolkitスキルを呼び出した痕跡があるか確認する。"""
    return _has_tool_usage(transcript_path, "Skill", "skill", _AGENT_TOOLKIT_PATTERN)


def _approve() -> None:
    print(json.dumps({}, ensure_ascii=False))


def _emit_block(reason: str) -> None:
    """Stop hookで当該ターン継続を強制する誘導を返す。

    `stop_hook_active`保護で1回のみ発火する前提。
    """
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main() -> int:
    """dotfiles個人環境専用セッション振り返りを誘導するエントリポイント。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        _approve()
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        _approve()
        return 0

    # Stop hookが直前のターンで既にブロック済みの再呼び出し。
    # 同一判定を繰り返すと連続ブロック上限に達して強制終了するため、即座にapproveする。
    if payload.get("stop_hook_active") is True:
        _approve()
        return 0

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if not transcript_path:
        _approve()
        return 0

    if not _has_pyfltr_usage(transcript_path) and not _has_agent_toolkit_usage(transcript_path):
        _approve()
        return 0

    if is_pending_async_work(transcript_path):
        _approve()
        return 0

    # 振り返りスキル起動済みフラグはセッション状態ファイル経由で確認する。
    # 観測は個人フックPostToolUseが`session_review_invoked`辞書へ記録する。
    # 新規作業区切りでのリセットは配布物PostToolUse(EnterPlanMode)が担う。
    state = read_state(session_id)
    invoked = state.get("session_review_invoked")
    if isinstance(invoked, dict) and invoked.get(_EXTENSION_SKILL) is True:
        _approve()
        return 0

    # 振り返り手順全体は `session-review-dotfiles` スキルおよび併用する
    # `agent-toolkit:session-review` スキルが保持する。本 hook は両スキルの併用呼び出しの前段に
    # SESSION_REVIEW_PRECHECK を付与し、満たさない場合はスキル起動自体を抑止する。
    # precheckを満たした場合も各スキル本体の起動方針節に従う。
    body = (
        f"{SESSION_REVIEW_PRECHECK} Then invoke the `{_EXTENSION_SKILL}` Skill via the Skill tool together with the"
        " `agent-toolkit:session-review` Skill in the same turn, and produce a single combined review covering"
        " both the dotfiles-specific extension chapters and the standard agent-toolkit session review."
        " Follow each skill's activation policy section to decide whether to proceed with the review."
    )
    _emit_block(_llm_notice(body))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
