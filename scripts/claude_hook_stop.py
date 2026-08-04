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

配布物側の抑止経路は`session_review_extension_pending`に加え、
環境変数`AGENT_TOOLKIT_SESSION_REVIEW_EXTENSION`の観測を持つ。
当該環境変数は`share/claude_settings_json_managed.json`の`env`で配布し、
本フックが導入された環境であることを配布物側へ伝える。

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

対象スキル（`session-review-dotfiles`）は`session_review_invoked`辞書経由の起動済み
フラグに加え、transcript内のユーザーターンに`<command-name>/session-review-dotfiles</command-name>`が
含まれるスラッシュコマンド起動痕跡（`_stop_gate.has_command_invocation`）でも
起動済み扱いとする。PostToolUse側のフラグ記録がスラッシュコマンド起動時のツール呼び出し
扱いを取りこぼす場合の代替経路。

各判定分岐の最終判定ラベルと根拠は`agent-toolkit/scripts/_stop_gate.append_stop_log`で
常時ログへ記録する。
"""

import collections.abc
import json
import pathlib
import re
import sys

# agent-toolkit の共通ゲートモジュールを import する。
# plugin が無効化されていても dotfiles リポジトリ上にファイルが存在し続けるため import は成立する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _message_format import SESSION_REVIEW_PRECHECK  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    append_stop_log,
    has_command_invocation,
    is_pending_async_work,
)

# pylint: disable-next=wrong-import-position,import-error
from _stop_gate import parse_stop_session as _parse_stop_session  # noqa: E402

# pylint: disable-next=wrong-import-position,import-error
from _transcript import iter_assistant_content_blocks as _iter_assistant_content_blocks  # noqa: E402

# `\bpyfltr\b` に相当するBashコマンド用の正規表現。
# uv run pyfltr / pyfltr / uv run --script ... pyfltr など典型的な呼び出し形式を網羅する。
_PYFLTR_PATTERN = re.compile(r"\bpyfltr\b")

# agent-toolkitプラグインに同梱するpyfltr MCPツール名の接頭辞。
_PYFLTR_MCP_TOOL_PREFIX = "mcp__plugin_agent-toolkit_pyfltr__"

# agent-toolkit スキル呼び出しを検出する正規表現。
# Skill ツールの input.skill フィールドに `agent-toolkit:` が含まれるケースを対象とする。
_AGENT_TOOLKIT_PATTERN = re.compile(r"\bagent-toolkit:")

# transcript内のユーザーターンでスラッシュコマンド起動痕跡を検出する正規表現。
_SESSION_REVIEW_DOTFILES_COMMAND_RE = re.compile(r"<command-name>/session-review-dotfiles</command-name>")
_SESSION_REVIEW_COMMAND_RE = re.compile(r"<command-name>/agent-toolkit:session-review</command-name>")

# このスクリプトの hook 識別子。
_HOOK_ID = "dotfiles/claude_hook_stop"

# 拡張章スキル名。本hookと同期対象のSKILL.md側でも参照される。
_EXTENSION_SKILL = "session-review-dotfiles"
_TARGET_SESSION_REVIEW = "agent-toolkit:session-review"


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
    for _position, block in _iter_assistant_content_blocks(lines):
        if block.get("type") == "tool_use":
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
    """Transcript内にpyfltrをBashまたはMCP経由で実行した痕跡があるか確認する。"""
    if _has_tool_usage(transcript_path, "Bash", "command", _PYFLTR_PATTERN):
        return True
    return any(
        isinstance(name := block.get("name"), str) and name.startswith(_PYFLTR_MCP_TOOL_PREFIX)
        for block in _iter_tool_use_blocks(transcript_path)
    )


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


def main(payload_text: str) -> int:
    """dotfiles個人環境専用セッション振り返りを誘導するエントリポイント。"""
    resolved = _parse_stop_session(payload_text, _approve)
    if resolved is None:
        return 0
    session_id, payload = resolved

    # Stop hookが直前のターンで既にブロック済みの再呼び出し。
    # 同一判定を繰り返すと連続ブロック上限に達して強制終了するため、即座にapproveする。
    if payload.get("stop_hook_active") is True:
        append_stop_log(session_id, "approve_stop_hook_active", {"stop_hook_active": True})
        _approve()
        return 0

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if not transcript_path:
        append_stop_log(session_id, "approve_no_transcript", {})
        _approve()
        return 0

    # 振り返りスキル起動済みフラグはセッション状態ファイル経由で確認する。
    # 観測は個人フックPostToolUseが`session_review_invoked`辞書へ記録するほか、
    # スラッシュコマンド起動痕跡（transcript走査）でも代替検出する。
    state = read_state(session_id)
    invoked = state.get("session_review_invoked")
    state_invoked = isinstance(invoked, dict) and invoked.get(_EXTENSION_SKILL) is True
    command_invoked = has_command_invocation(transcript_path, _SESSION_REVIEW_DOTFILES_COMMAND_RE)
    target_state_invoked = isinstance(invoked, dict) and invoked.get(_TARGET_SESSION_REVIEW) is True
    target_command_invoked = has_command_invocation(transcript_path, _SESSION_REVIEW_COMMAND_RE)

    if not any(
        (
            _has_pyfltr_usage(transcript_path),
            _has_agent_toolkit_usage(transcript_path),
            state_invoked,
            command_invoked,
            target_state_invoked,
            target_command_invoked,
        )
    ):
        append_stop_log(session_id, "approve_no_pyfltr", {})
        _approve()
        return 0

    if is_pending_async_work(transcript_path, session_id):
        append_stop_log(session_id, "approve_pending_async", {})
        _approve()
        return 0

    if state_invoked or command_invoked:
        append_stop_log(
            session_id,
            "approve_review_invoked",
            {"session_review_invoked": state_invoked, "command_detected": command_invoked},
        )
        _approve()
        return 0

    # 振り返り手順全体は`session-review-dotfiles`スキルおよび併用する
    # `agent-toolkit:session-review`スキルが保持する。本hookは起動済み状態に応じた呼び出しの前段に
    # SESSION_REVIEW_PRECHECKを付与し、満たさない場合はスキル起動自体を抑止する。
    # precheckを満たした場合も各スキル本体の起動方針節に従う。
    target_invoked = target_state_invoked or target_command_invoked
    if target_invoked:
        body = (
            f"{SESSION_REVIEW_PRECHECK} If so, `{_TARGET_SESSION_REVIEW}` has already been invoked in this"
            f" session but `{_EXTENSION_SKILL}` has not. Invoke `{_EXTENSION_SKILL}` via the Skill tool now"
            " and merge both reports into one, per each skill's activation policy section."
        )
    else:
        body = (
            f"{SESSION_REVIEW_PRECHECK} If so, invoke `{_EXTENSION_SKILL}` via the Skill tool together with"
            f" `{_TARGET_SESSION_REVIEW}` in the same turn as one combined review,"
            " per each skill's activation policy section."
        )
    append_stop_log(session_id, "block_session_review", {})
    _emit_block(_llm_notice(body))
    return 0
