"""証拠抽出の問題候補（session_evidence_candidates_failures）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from session_evidence_candidates_commands import (
    _SHELL_OPERATOR_CHARS,
    _event_without_cd_prefix,
    _failure_command_parts,
    _failure_exit_code,
    _is_check_detected,
    _is_negative_search_command,
    _shell_command_tokens,
    _single_read_predicate,
)
from session_evidence_candidates_hooks import _hook_originated_event, _same_hook_event
from session_evidence_extract import _HOOK_FAILURE_PREFIX, _OMISSION_MARK, _json_object

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common.bash_invocations import is_read_predicate

_PERMISSION_DENIAL_MARKER = "denied by the Claude Code auto mode classifier"
"""auto mode classifierの拒否本文に現れる定型句。実行環境が返す本文をそのまま用いる。"""


_TRANSIENT_CLASSIFIER_ERROR = "The server-side auto mode classifier gave no verdict (error)"


_FAILURE_PATH = re.compile(r"(?<!\w)(?:~?/|[A-Za-z]:[\\/])[^\s'\"`]+")


_FAILURE_RECORD_ID = re.compile(r"\b(?:codex|claude|agy|opencode):[A-Za-z0-9_-]+(?::\d+)?")


_FAILURE_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


FAILURE_CANDIDATE_KINDS = frozenset({"command-failure", "tool-failure"})
"""失敗署名で集約する候補種別。"""


FAILURE_EVENT_FIELDS = (
    "tool",
    "tool_name",
    "exit_code",
    "text",
    "diagnostic",
    "diagnostic_last_line",
    "operation",
    "command",
    "command_full",
    "record",
    "line",
    "hook_notices",
)
"""失敗候補の分類と署名が読むイベントの欄。hook_noticesは同じ位置の通知のtextとhook_nameを保持する。

失敗候補の`failure_event`へ代表イベントから写し、失敗署名の台帳はこの欄を保存して読込時に判定し直す。
"""


def _is_permission_denial(event: dict[str, Any]) -> bool:
    """Auto mode classifierの拒否で終わったツール実行であるかを返す。

    拒否は`is_error`のtool_resultとして記録されるため`failed-tool`の一部に当たる。
    許可ルールの見直しという他の失敗とは別の是正へ結び付くため、独立した候補種別として扱う。
    """
    if event.get("kind") != "failed-tool":
        return False
    text = event.get("text")
    return isinstance(text, str) and _PERMISSION_DENIAL_MARKER in text


def classify_failure_candidate(candidate_kind: str, event: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """抽出と台帳で共通の入力から、除外区分または置換先のhook通知を返す。"""
    if candidate_kind == "tool-failure":
        notices = event.get("hook_notices", [])
        if isinstance(notices, list) and any(
            isinstance(notice, dict) and _same_hook_event(candidate_kind, event, notice) for notice in notices
        ):
            return "hook-notice-represented", None
        hook_event = _hook_originated_event(candidate_kind, event, [])
        if hook_event is not None:
            exclusion = _failure_result_exclusion(candidate_kind, _event_without_cd_prefix(event))
            return exclusion, hook_event
    return failure_candidate_exclusion(candidate_kind, event), None


def failure_candidate_exclusion(candidate_kind: str, event: dict[str, Any]) -> str | None:
    """失敗候補から除く区分を、1件のイベントの欄（`FAILURE_EVENT_FIELDS`）だけで判定して返す。除かない場合は`None`を返す。

    候補の抽出と失敗署名の台帳の読込が同じ判定を使い、除外規則の改訂を記録済みの台帳にも次の準備から適用する。
    hook通知との対応と置き換えを含む共通の分類は`classify_failure_candidate`が行う。
    """
    # 除外の判定は終了コードを帰属させるコマンドで行い、候補の署名と表示は記録どおりのイベントで行う。
    attributed = _event_without_cd_prefix(event)
    exclusion = _failure_result_exclusion(candidate_kind, attributed)
    if exclusion is not None:
        return exclusion
    if candidate_kind == "command-failure" and _is_help_command_failure(event):
        return "command-help"
    if candidate_kind == "command-failure" and _is_normal_negative_result(attributed):
        return "normal-negative-result"
    text = event.get("text")
    if candidate_kind == "tool-failure" and isinstance(text, str) and _TRANSIENT_CLASSIFIER_ERROR in " ".join(text.split()):
        return "runtime-transient"
    if _is_check_detected(attributed):
        return "check-detected"
    return None


def _failure_result_exclusion(candidate_kind: str, attributed: dict[str, Any]) -> str | None:
    """終了コードを帰属させたイベントが正常な否定結果か待機継続である場合に、その区分を返す。

    hook通知へ置き換える失敗にも適用するため、`failure_candidate_exclusion`から分けている。
    """
    if candidate_kind == "tool-failure" and _is_normal_negative_tool_failure(attributed):
        return "normal-negative-result"
    if _is_normal_atk_no_match(attributed):
        return "normal-negative-result"
    if _is_normal_nonterminal_result(attributed):
        return "normal-nonterminal-result"
    return None


def _is_help_command_failure(event: dict[str, Any]) -> bool:
    """変更処理へ到達せずusageを返した明示的なCLIヘルプ取得であるかを返す。"""
    command_value = event.get("command")
    diagnostic = event.get("diagnostic")
    if not isinstance(command_value, str) or not isinstance(diagnostic, str):
        return False
    try:
        command = json.loads(command_value)
    except json.JSONDecodeError:
        return False
    if not isinstance(command, list) or not all(isinstance(argument, str) for argument in command):
        return False
    if not any(argument in {"-h", "--help"} for argument in command[1:]):
        return False
    normalized = diagnostic.casefold()
    return "usage:" in normalized or "options:" in normalized


def _is_normal_nonterminal_result(event: dict[str, Any]) -> bool:
    """単独の`atk agents wait`が返す終了3を、正常な待機継続として区分する。

    先頭の`cd`の前置は、呼び出し側が`_event_without_cd_prefix`で外してから渡す。
    """
    if _failure_exit_code(event) != 3:
        return False
    executable, subcommand, command, args = _failure_command_parts(event)
    if executable != "atk" or subcommand != "agents" or args[1:3] != ["agents", "wait"]:
        return False
    tokens = _shell_command_tokens(command)
    # 連結全体の終了コードを待機へ帰属させず、同じコマンドによる継続だけを除外する。
    # 演算子形のデータを含む語も従来どおり除外の対象外とし、待機継続の除外範囲を広げない。
    if tokens is None:
        return False
    return not any(set(token.value) <= _SHELL_OPERATOR_CHARS for token in tokens)


def _is_normal_negative_result(event: dict[str, Any]) -> bool:
    """読取専用の述語が診断なしで偽を返した事象を区分する。"""
    exit_code = event.get("exit_code")
    if not isinstance(exit_code, int) or str(event.get("diagnostic", "")).strip():
        return False
    command_value = event.get("command")
    if not isinstance(command_value, str):
        return False
    try:
        args = json.loads(command_value)
    except json.JSONDecodeError:
        return False
    if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
        return False
    executable = Path(args[0]).name
    if executable in {"bash", "sh", "zsh"}:
        if len(args) < 3 or args[1] not in {"-c", "-lc"}:
            return False
        tokens = _shell_command_tokens(args[2])
        return (
            _single_read_predicate(args[2], exit_code) or tokens is not None and _is_negative_search_command(tokens, exit_code)
        )
    return exit_code == 1 and is_read_predicate(args)


def _is_normal_atk_no_match(event: dict[str, Any]) -> bool:
    """単独のatkが結果行と終了1で表す該当0件を、生成側の契約から判定する。

    先頭の`cd`の前置は、呼び出し側が`_event_without_cd_prefix`で外してから渡す。
    """
    if event.get("tool") != "CommandExecution" and event.get("tool_name") != "Bash":
        return False
    if _failure_exit_code(event) != 1:
        return False
    executable, _subcommand, command, _args = _failure_command_parts(event)
    if executable != "atk":
        return False
    # 包装の外側の連結も、解除したshellの内部の連結も、終了コードをatkへ帰属できない。
    operation = _json_object(str(event.get("operation", "")))
    original_command = operation.get("command") if operation is not None else None
    commands = [command]
    if isinstance(original_command, str):
        commands.append(original_command)
    for source in commands:
        tokens = _shell_command_tokens(source)
        if tokens is None or any(token.operator for token in tokens):
            return False
    output = event.get("diagnostic") if event.get("tool") == "CommandExecution" else event.get("text")
    if not isinstance(output, str) or _OMISSION_MARK in output:
        return False
    lines = [line.lstrip() for line in output.splitlines()]
    return any(line.startswith(_outcome.NO_MATCH_PREFIX) for line in lines) and not any(
        line.startswith((_outcome.FAILURE_PREFIX, _outcome.WARNING_PREFIX)) for line in lines
    )


def _is_normal_negative_tool_failure(event: dict[str, Any]) -> bool:
    """Claude CodeのBashで、読取専用の述語または検索が出力なしで偽を返した事象を区分する。

    Claude Codeは出力の無い非0終了を`Exit code <N>`だけの失敗として記録する。
    `&&`の全段が読取専用の述語であれば、どの段が偽でも正常な否定結果とする。
    """
    matched = re.fullmatch(r"Exit code (\d+)", str(event.get("text", "")).strip())
    if event.get("tool_name") != "Bash" or matched is None:
        return False
    try:
        operation = json.loads(str(event.get("operation", "")))
    except json.JSONDecodeError:
        return False
    command = operation.get("command") if isinstance(operation, dict) else None
    if not isinstance(command, str):
        return False
    tokens = _shell_command_tokens(command)
    code = int(matched.group(1))
    return _single_read_predicate(command, code) or tokens is not None and _is_negative_search_command(tokens, code)


def _failure_diagnostic(event: dict[str, Any]) -> str:
    tail = event.get("diagnostic_last_line")
    if isinstance(tail, str) and tail.strip():
        return tail.strip()
    text = str(event.get("diagnostic") or event.get("text") or "")
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def failure_signature(candidate_kind: str, event: dict[str, Any]) -> tuple[str, str]:
    """可変値を除いた失敗署名と、`candidates.md`へ表示するコマンド・診断を返す。"""
    name, subcommand, display, _ = _failure_command_parts(event)
    code = _failure_exit_code(event)
    diagnostic = _failure_diagnostic(event)
    if candidate_kind == "tool-failure" and "hook error:" in diagnostic:
        diagnostic = _HOOK_FAILURE_PREFIX.sub("", diagnostic, count=1)
    if diagnostic == "CommandExecution failed" and not display:
        diagnostic = ""
    normalized = normalize_failure_diagnostic(diagnostic)
    if not normalized:
        normalized = display or f"{event.get('record', '')}:{event.get('line', '')}"
    signature = json.dumps([candidate_kind, name, subcommand, code, normalized], ensure_ascii=False, separators=(",", ":"))
    summary = f"{display or name}（終了コード{code if code is not None else '不明'}）: {diagnostic or '診断なし'}"
    return signature, summary


def normalize_failure_diagnostic(diagnostic: str) -> str:
    """診断の理由を残し、対象と識別できるパス・記録ID・UUIDだけを一般化する。"""
    normalized = " ".join(diagnostic.split()).casefold()
    normalized = _FAILURE_RECORD_ID.sub("<record>", normalized)
    normalized = _FAILURE_UUID.sub("<uuid>", normalized)
    return _FAILURE_PATH.sub("<path>", normalized)


def recalculate_failure_signature(signature: str, summary: str) -> str:
    """既存台帳の署名軸と保存済み代表診断から、現在の理由の正規化で署名を再計算する。"""
    parts = json.loads(signature)
    if not isinstance(parts, list) or len(parts) != 5:
        raise ValueError("失敗署名の軸が不正")
    diagnostic = re.search(r"（終了コード(?:\d+|不明)）: (.*)\Z", summary, re.DOTALL)
    if diagnostic is None:
        raise ValueError("失敗の代表診断の形式が不正")
    normalized = normalize_failure_diagnostic(diagnostic[1])
    if normalized and normalized != "診断なし":
        parts[4] = normalized
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
