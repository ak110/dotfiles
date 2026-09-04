r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録とplan file形式検査。

Bash / Write / Edit / MultiEdit / apply_patch / Skill / Read / Agent / Taskの実行後に
イベントを検出し、セッション状態ファイルに記録する。
PreToolUseやStopフックが参照して警告・提案の判定に使う。

編集入力は`_hook_tool_input`が共通の操作記録へ正規化する。
Codexでは成功した`apply_patch`だけが本フックへ届く。Bashは終了コードを取得できないため、
`git log`確認・amend・push・検証実行の成功状態を記録しない。

検出対象:

1. テスト実行 (Bash / pyfltr MCPの`run_for_agent`)
2. git log確認状態の記録・リセット (Bash: logで記録、対象コミットの親子関係が
   変化する操作＝commit/rebase/resetでリセット)
3. plan file（計画作業root `~/.claude/plans/` または
   保存済み計画root `$(atk config get private_notes)/plans/` 配下）形式検査 (Write / Edit / MultiEdit / apply_patch)
4. plan-modeスキル呼び出し検出 (Skill)
5. 計画実行系`model_type`の`agents_server` sessionの起動時刻と終了時刻の`_process_loop_log`記録
6. agents_server MCP呼び出し後のsession状態記録
7. exit-session起動検知による`autonomous_exit_invoked`の記録と
   `process_feedbacks_skill_invoked`のリセット (Skill)
8. 現在の計画ファイルパス記録 (Write / Edit / MultiEdit、plan file判定時)
   （pretooluse.py側の遡及スキャン記録検査が計画ファイル本文を再読み込みする際に使用）
9. 編集ファイルパス蓄積（Write / Edit / MultiEdit、`session_edited_files`リストへ追記）
   （pretooluse.py側の一括ステージ警告で自セッション編集対象の判定に使用）
10. `git commit --amend` / `git commit --fixup` 成功時のcwd別
    `amend_pending_status_check`フラグ設定（pretooluse.py側の`git push`前dirty検査で参照）
11. `git push`（`--dry-run` / `-n`以外）成功時の該当cwd`amend_pending_status_check`フラグ解除
12. PostToolUseFailure・PermissionDenied: 原則状態を変更せず終了
13. 条件付き禁止形（「〜した状態で…しない/禁止」）の警告検出 (Write / Edit / MultiEdit、
    `is_agent_facing_md`が対象と判定するコーディングエージェント向け`.md`編集時)
14. 対象リポジトリで新たに回答されたTBDファイルの通知（全ツール共通）
15. 当該セッションで作成又は編集した計画ファイル（メイン）の絶対パス蓄積
    （編集ツールの操作記録と`create_plan_files.py`のBash標準出力）
"""

import json
import pathlib
import re
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills" / "plan-mode" / "scripts"))
import _agents_server_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _hook_tool_input  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _process_loop_log  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _stop_gate  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _uwi_completion  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_git_events,
)
from _hook_agent_id import resolve_hook_agent_id  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _hook_notice import formatter as _notice_formatter  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_component_file,
    is_plan_main_file,
)
from _plan_format import is_agent_facing_md  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable=wrong-import-position,import-error
from _tracked_model_types import TRACKED_MODEL_TYPES as _TRACKED_MODEL_TYPES  # noqa: E402

# pylint: enable=wrong-import-position,import-error

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/posttooluse"

# agent-toolkitプラグインに同梱するpyfltr MCPの検証実行ツール名。
# hooks/hooks.jsonのPostToolUse matcherと同一値を保つ。
_PYFLTR_RUN_FOR_AGENT_TOOL_NAME = "mcp__plugin_agent-toolkit_pyfltr__run_for_agent"

_llm_notice = _notice_formatter(_HOOK_ID)


# --- Bashコマンド前処理 ---

# コマンド先頭またはセグメント区切り（`;`・`&`・`|`）直後に並ぶ接頭辞を捕捉する。
# 対象は`KEY=VALUE`の環境変数代入と`timeout <時間>`の時間制限で、両者の混在と連続も1回で除去する。
# `.sub`で接頭辞列を除去し、先頭の区切り文字＋空白は維持する。
_COMMAND_PREFIX_PATTERN = re.compile(r"(\A|[;&|])(\s*)(?:[A-Za-z_]\w*=\S*\s+|timeout\s+\d+(?:\.\d+)?[smhd]?\s+)+")


def _strip_command_prefixes(command: str) -> str:
    """コマンド先頭・セグメント区切り直後の環境変数代入と時間制限の接頭辞を除去する。

    用途: テスト実行検出やgit操作検出の正規表現が、`LOCALAPPDATA=/tmp/dummy uvx pyfltr ...`や
    `timeout 600 uvx pyfltr run ...`のような接頭辞付きコマンドにマッチせず、
    検証済みでも未検証として警告される問題に追従する。
    適用範囲: Bashコマンド文字列。`KEY=VALUE`と`timeout <時間>`の単純形式のみを対象とし、
    クォート内に空白を含む値・`env`コマンド経由・行継続バックスラッシュ・
    `timeout`のオプション付き形式（`-k 10s 600`等、引数の境界を字句だけで確定できない）は対象外とする。
    """
    return _COMMAND_PREFIX_PATTERN.sub(r"\1\2", command)


# --- テスト実行検出パターン ---

_TEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 直接実行系
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+)?(?:python\s+-m\s+)?pytest\b"),
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+|uvx\s+)?pyfltr\s+(?:run|ci|fast|agent)\b"),
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+|uvx\s+)?(?:pre-commit|prek)\s+run\b"),
    re.compile(r"(?:^|[;&|]\s*)cargo\s+test\b"),
    # タスクランナー経由（make / mise run / npm | pnpm | yarn（run省略可）/ just / task）で
    # test / check / validateアクション
    re.compile(
        r"(?:^|[;&|]\s*)"
        r"(?:make\s+|(?:npm|pnpm|yarn)\s+(?:run\s+)?|mise\s+run\s+|just\s+|task\s+)"
        r"(?:test|check|validate)\b"
    ),
)

# --- git関連サブコマンドの分類 ---

# git_log_checked をリセットするサブコマンド（対象コミットの親子関係が変化する操作に限定する。
# `push`は既存コミットを送出するのみで親子関係を変えないためリセット対象から除外する）。
_GIT_LOG_RESET_SUBCOMMANDS: frozenset[str] = frozenset({"commit", "rebase", "reset"})


def _set_amend_pending_status_check(state: dict, cwd: str) -> dict | None:
    """Git commit --amend / --fixup 成功時にcwd別フラグを設定する。既にTrueならNoneを返す（冪等）。"""
    flags = state.get(_git_status.AMEND_PENDING_FLAG_KEY)
    if not isinstance(flags, dict):
        flags = {}
    if flags.get(cwd, False):
        return None
    flags[cwd] = True
    state[_git_status.AMEND_PENDING_FLAG_KEY] = flags
    return state


def _reset_amend_pending_status_check(state: dict, cwd: str) -> dict | None:
    """該当cwdでpush前検査を通過した時点、またはpush成功時にフラグを解除する。既にFalseならNoneを返す（冪等）。"""
    flags = state.get(_git_status.AMEND_PENDING_FLAG_KEY)
    if not isinstance(flags, dict) or not flags.get(cwd, False):
        return None
    flags[cwd] = False
    state[_git_status.AMEND_PENDING_FLAG_KEY] = flags
    return state


def _git_commit_is_amend_or_fixup(args: list[str]) -> bool:
    """`git commit`のサブコマンド引数列から`--amend` / `--fixup=<sha>` / `--fixup <sha>`を検出する。"""
    for tok in args:
        if tok == "--amend":
            return True
        if tok == "--fixup" or tok.startswith("--fixup="):
            return True
    return False


# --- plan-modeスキル呼び出し検出 ---

# Skillツールの`skill`引数として許容するスキル名。
# ユーザーが手動で短縮名を渡すケースに備えてフルネームと短縮名の両方を許容する。
_PLAN_MODE_SKILL_NAMES = frozenset({"agent-toolkit:plan-mode", "plan-mode"})

# process-feedbacksスキル呼び出し検出。フルネームとスラッシュコマンド短縮名の両方を許容する。
_PROCESS_FEEDBACKS_SKILL_NAMES = frozenset({"agent-toolkit:process-feedbacks", "process-feedbacks"})

# exit-sessionスキル呼び出し検出。process-feedbacksのフラグリセット経路に使う
# （`agent-toolkit:process-feedbacks`の`references/finish-session.md`がexit-sessionで終端する）。
_EXIT_SESSION_SKILL_NAMES = frozenset({"agent-toolkit:exit-session", "exit-session"})
_AUTONOMOUS_EXIT_STATE_KEY = "autonomous_exit_invoked"

# Claude CodeとCodexが生成するagents_serverの完全修飾MCP tool名。
_AGENTS_SERVER_NAMESPACES = (
    "mcp__plugin_agent-toolkit_agents_server__",
    "mcp__agents_server__",
)
_AGENTS_SERVER_START_TOOLS = frozenset(
    f"{namespace}{tool}" for namespace in _AGENTS_SERVER_NAMESPACES for tool in ("start", "start_explore", "start_shell")
)
_AGENTS_SERVER_WAIT_TOOLS = frozenset(f"{namespace}wait" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_SEND_TOOLS = frozenset(f"{namespace}send_message" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_KILL_TOOLS = frozenset(f"{namespace}kill" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_TOOL_NAMES = (
    _AGENTS_SERVER_START_TOOLS | _AGENTS_SERVER_WAIT_TOOLS | _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS
)
_AGENTS_SERVER_DIAGNOSTIC_TOOLS = _AGENTS_SERVER_TOOL_NAMES

_AGENTS_SERVER_SESSION_CWD_KEY = "agents_server_cwd_by_session"
_AGENTS_SERVER_SESSION_STATE_KEY = "agents_server_sessions"


# 条件付き禁止形（「〜した状態で…しない/禁止」）検出パターン。
# 「Xした状態でYしない」形式は「Xでなければ`Y`してよい」と誤読され得るため、
# 全称否定形（「いかなる理由があっても`Y`しない」）または肯定的完遂義務への
# 書き換えを促す。初期段階の限定的なパターンであり、将来の検出範囲拡張は拡張候補とする。
# 全角鍵括弧・バッククォート囲みの引用文脈（他ファイル節名・識別子・規範文言の引用）は
# 照合前に無害化する。本実装は行番号算出（`content`上のオフセットをそのまま使う）を成立させるため文字数を保ったまま
# 改行以外を空白へ置換する（除去着想のみ同関数を参考にし、実装は異なる）。
_CONDITIONAL_PROHIBITION_RE = re.compile(r"[^\n]{1,30}?した状態で[^\n]{0,30}?(しない|禁止)")
_CONDITIONAL_PROHIBITION_KAKKO_RE = re.compile(r"「[^」]*」|『[^』]*』")
_CONDITIONAL_PROHIBITION_BACKTICK_RE = re.compile(r"`[^`\n]+`")


def _blank_out_preserving_length(match: re.Match[str]) -> str:
    """マッチ区間を、改行はそのまま・それ以外は半角空白へ置換し文字数を保つ。"""
    return "".join(ch if ch == "\n" else " " for ch in match.group())


def _check_conditional_prohibition(file_path: pathlib.Path, content: str) -> list[str]:
    """条件付き禁止形（「〜した状態で…しない/禁止」）を警告として検出する。"""
    excluded = _CONDITIONAL_PROHIBITION_BACKTICK_RE.sub(
        _blank_out_preserving_length,
        _CONDITIONAL_PROHIBITION_KAKKO_RE.sub(_blank_out_preserving_length, content),
    )
    warnings: list[str] = []
    for m in _CONDITIONAL_PROHIBITION_RE.finditer(excluded):
        line_num = content[: m.start()].count("\n") + 1
        warnings.append(
            f"{file_path}:{line_num}: 条件付き禁止形（「〜した状態で…しない」）を検出。"
            f"全称否定形（「いかなる理由（例: X）があっても...しない」）"
            f"または肯定的完遂義務への書き換えを検討する"
        )
    return warnings


# --- plan file形式検査の定数 ---


def _set_process_feedbacks_invoked(state: dict) -> dict | None:
    """process-feedbacksスキル起動フラグを常時Trueへ上書きする。

    新規process-feedbacksラン開始時に前ランの残置フラグを無視して確実にTrueへ強制上書きするため冪等スキップを廃止する。
    リセット経路は`_reset_process_feedbacks_invoked`（exit-session起動検知）と併用する。
    """
    state["process_feedbacks_skill_invoked"] = True
    return state


def _reset_process_feedbacks_invoked(state: dict) -> dict | None:
    """`process_feedbacks_skill_invoked`を偽へ戻す。既に偽ならNoneを返す（冪等）。"""
    if not state.get("process_feedbacks_skill_invoked", False):
        return None
    state["process_feedbacks_skill_invoked"] = False
    return state


def _record_exit_session_invoked(state: dict) -> dict | None:
    """exit-session呼び出しを記録し、`process_feedbacks_skill_invoked`をリセットする。"""
    changed = False
    if state.get(_AUTONOMOUS_EXIT_STATE_KEY) is not True:
        state[_AUTONOMOUS_EXIT_STATE_KEY] = True
        changed = True
    if _reset_process_feedbacks_invoked(state) is not None:
        changed = True
    return state if changed else None


def _extract_agents_server_structured_response(tool_response: object) -> dict:
    """agents_server応答のdictまたはJSON文字列を状態記録用へ正規化する。"""
    if isinstance(tool_response, dict):
        structured = tool_response.get("structuredContent")
        return structured if isinstance(structured, dict) else tool_response
    if isinstance(tool_response, str):
        try:
            parsed = json.loads(tool_response)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_nonempty_absolute_cwd(value: object) -> bool:
    """cwdが空白でない絶対パスであることを判定する。"""
    return isinstance(value, str) and bool(value.strip()) and pathlib.PurePath(value).is_absolute()


def _agents_server_recorded_cwd(session_id: str, payload: dict, structured: dict, tool_name: str) -> object:
    """startの入力cwdまたはsessionごとのcwd mapから応答のcwd候補を取得する。"""
    tool_input = payload.get("tool_input")
    if tool_name in _AGENTS_SERVER_START_TOOLS:
        input_cwd = tool_input.get("cwd") if isinstance(tool_input, dict) else None
        return input_cwd if _is_nonempty_absolute_cwd(input_cwd) else None
    state = read_state(session_id)
    remote_session_id = structured.get("session_id")
    cwd_map = state.get(_AGENTS_SERVER_SESSION_CWD_KEY)
    return cwd_map.get(remote_session_id) if isinstance(cwd_map, dict) else None


def _agents_server_missing_response_fields(session_id: str, payload: dict, structured: dict, tool_name: str) -> list[str]:
    """成功した応答から状態記録に必要な欠落項目を列挙する。"""
    missing: list[str] = []
    if not structured:
        missing.append("response")
    for field in ("session_id", "status"):
        value = structured.get(field)
        if not isinstance(value, str) or not value.strip():
            missing.append(field)
    if tool_name in _AGENTS_SERVER_START_TOOLS and not _is_nonempty_absolute_cwd(
        _agents_server_recorded_cwd(session_id, payload, structured, tool_name)
    ):
        missing.append("cwd")
    return missing


def _record_agents_server_session_state(
    session_id: str,
    structured: dict,
    *,
    operation: str,
    owner_agent_id: str,
    cwd: str | None = None,
    model_type: str | None = None,
) -> None:
    """agents_serverの公開応答をhook側の状態へ記録する。"""
    remote_session_id = structured.get("session_id")
    status = structured.get("status")
    if not isinstance(remote_session_id, str) or not remote_session_id or not isinstance(status, str):
        return

    def _mutator(state: dict) -> dict | None:
        sessions = state.setdefault(_AGENTS_SERVER_SESSION_STATE_KEY, {})
        previous = sessions.get(remote_session_id)
        previous = previous if isinstance(previous, dict) else {}
        record = dict(previous)
        record.pop("cwd", None)
        record.pop("_".join(("result", "retrieved")), None)
        record.update({"session_id": remote_session_id, "status": status})
        if model_type is not None:
            record["model_type"] = model_type
        if operation in {"start", "start_explore", "start_shell"}:
            record["pending_observation"] = True
            record["owner_agent_id"] = owner_agent_id
        elif operation == "send_message":
            delivery = structured.get("delivery")
            if delivery in {"steered", "reply_started", "reply_ambiguous"}:
                record["pending_observation"] = True
                record["owner_agent_id"] = owner_agent_id
        elif operation in {"wait", "kill"}:
            record["pending_observation"] = False
        kill_requested = structured.get("kill_requested")
        if isinstance(kill_requested, bool):
            record["kill_requested"] = kill_requested
        turn_id = structured.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            record["turn_id"] = turn_id
        if status == "running":
            record.pop("error", None)
        elif structured.get("error") is not None:
            record["error"] = structured["error"]
        if structured.get("agent_message") is not None:
            record["agent_message"] = structured["agent_message"]
        changed = sessions.get(remote_session_id) != record
        if changed:
            sessions[remote_session_id] = record
        if isinstance(cwd, str) and cwd:
            cwd_map = state.setdefault(_AGENTS_SERVER_SESSION_CWD_KEY, {})
            if cwd_map.get(remote_session_id) != cwd:
                cwd_map[remote_session_id] = cwd
                changed = True
        return state if changed else None

    update_state(session_id, _mutator)


def _log_tracked_session_end(session_id: str, structured: dict) -> None:
    """終端した計画実行系sessionの終了時刻をprocess-loopの観測ログへ記録する。

    `model_type`は`start`応答にだけ現れるため、起動時に保持した記録から取得する。
    """
    if structured.get("status") not in _agents_server_state.TERMINAL_STATUSES:
        return
    remote_session_id = structured.get("session_id")
    sessions = read_state(session_id).get(_AGENTS_SERVER_SESSION_STATE_KEY)
    record = sessions.get(remote_session_id) if isinstance(sessions, dict) else None
    model_type = record.get("model_type") if isinstance(record, dict) else None
    if model_type in _TRACKED_MODEL_TYPES:
        _process_loop_log.append("subagent_end", type=model_type)


def _record_agents_server_observation_attempt(session_id: str, tool_input: dict, *, operation: str) -> None:
    """背景タスクへ移った`wait`・`kill`の移行通知から観測の試みだけを記録する。

    実行環境が呼び出しを背景タスクへ移すと構造化応答が返らないため、応答の`session_id`と
    `status`を入力とする`_record_agents_server_session_state`は何も更新せずに戻る。
    呼び出しの受理をもって観測を試みたものとして扱い、応答境界へ到達しない経路でも
    `pending_observation`を偽にする。対象は`tool_input`の`session_id`で解決した既存記録に限り、
    記録が無いsessionへ新規の記録を作成しない。`status`・`turn_id`・`kill_requested`などの
    公開状態は移行通知から確定できないため更新しない。
    """
    if operation not in {"wait", "kill"}:
        return
    remote_session_id = tool_input.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        return

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
        record = sessions.get(remote_session_id) if isinstance(sessions, dict) else None
        if not isinstance(record, dict) or record.get("pending_observation") is False:
            return None
        record["pending_observation"] = False
        return state

    update_state(session_id, _mutator)


def _parse_hook_payload(payload_text: str) -> tuple[dict, str, str, dict, str] | None:
    """処理対象のPostToolUse payloadを検証して共通項目を返す。"""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return None
    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return None
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    event_name = payload.get("hook_event_name", "")
    if event_name in {"PostToolUseFailure", "PermissionDenied"}:
        return None
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    return payload, session_id, tool_name, tool_input, cwd


def _record_test_executed(session_id: str) -> None:
    """Pyfltr MCPの成功を検証実行済みとして記録する。"""

    def _set_test_executed(state: dict) -> dict | None:
        if state.get("test_executed", False):
            return None
        state["test_executed"] = True
        return state

    update_state(session_id, _set_test_executed)


_BACKGROUND_TASK_ID_RE = re.compile(r"running in background with ID:\s*([\w-]+)")


def _background_task_id_from_response(value: object) -> str | None:
    """Bashの背景実行応答からタスクIDを返す。

    応答は文字列と辞書のいずれの形でも届くため、入れ子を再帰的に走査する。
    """
    if isinstance(value, str):
        match = _BACKGROUND_TASK_ID_RE.search(value)
        return match.group(1) if match is not None else None
    if isinstance(value, dict):
        nested_values: list[object] = list(value.values())
    elif isinstance(value, list):
        nested_values = list(value)
    else:
        return None
    for nested in nested_values:
        task_id = _background_task_id_from_response(nested)
        if task_id is not None:
            return task_id
    return None


def _record_background_task_id(session_id: str, task_id: str) -> None:
    """自セッションが起動した背景タスクのIDを記録する。

    PreToolUse(TaskStop)が、停止対象が自セッションの起動した背景タスクかを判定する入力とする。
    """

    def _append(state: dict) -> dict | None:
        recorded = state.get("background_task_ids")
        recorded = list(recorded) if isinstance(recorded, list) else []
        if task_id in recorded:
            return None
        recorded.append(task_id)
        state["background_task_ids"] = recorded
        return state

    update_state(session_id, _append)


def _record_skill_use(session_id: str, skill_name: object) -> None:
    """Skill呼び出しに対応するセッション状態を記録する。"""
    if not isinstance(skill_name, str):
        return
    if skill_name in _PLAN_MODE_SKILL_NAMES:

        def _set_invoked(state: dict) -> dict | None:
            if state.get("plan_mode_skill_invoked", False):
                return None
            state["plan_mode_skill_invoked"] = True
            return state

        update_state(session_id, _set_invoked)
    if skill_name in _PROCESS_FEEDBACKS_SKILL_NAMES:
        update_state(session_id, _set_process_feedbacks_invoked)
    if skill_name in _EXIT_SESSION_SKILL_NAMES:
        update_state(session_id, _record_exit_session_invoked)


def _record_edited_file(session_id: str, file_path: str) -> None:
    """自セッションで編集したファイルを重複なしで記録する。"""
    if not file_path:
        return

    def _append_edited_file(current_state: dict) -> dict | None:
        edited = current_state.get("session_edited_files", [])
        if not isinstance(edited, list) or file_path in edited:
            return None
        edited.append(file_path)
        current_state["session_edited_files"] = edited
        return current_state

    update_state(session_id, _append_edited_file)


def _record_plan_file(session_id: str, file_path: str) -> None:
    """現在の計画ファイルを記録する。"""

    def _set_current_plan_file_path(current_state: dict) -> dict | None:
        if current_state.get("current_plan_file_path") == file_path:
            return None
        current_state["current_plan_file_path"] = file_path
        return current_state

    update_state(session_id, _set_current_plan_file_path)


def _handle_edit_tool(
    session_id: str,
    tool_name: str,
    tool_input: dict,
    cwd: str,
    notices: list[str],
) -> None:
    """編集成功後の状態記録と文書検査を処理する。

    ClaudeのWrite・Edit・MultiEditとCodexの成功した`apply_patch`を
    `_hook_tool_input`が共通の操作記録へ変換する。
    本フックは適用後に呼ばれるため変更前後像を再構築せず、操作記録のパスだけを状態へ記録する。
    実ファイルの読み込みを伴う文書検査は、適用後に存在する対象（追加・更新・移動先）へ限定する。
    """
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd)
    if operations is None:
        return
    state = read_state(session_id)
    plan_mode_invoked = bool(state.get("plan_mode_skill_invoked", False))
    for operation in operations:
        for display_path in operation.display_paths:
            _record_edited_file(session_id, display_path)
        if not operation.exists_after_apply:
            continue
        display_path = operation.display_path
        if is_plan_main_file(display_path):
            _record_plan_file(session_id, display_path)
        if is_agent_facing_md(display_path):
            _append_conditional_prohibition_notice(operation.path, display_path, notices)
        if plan_mode_invoked and is_plan_component_file(display_path) and operation.is_whole_write:
            notices.append(_plan_file_check_notice(_plan_main_path_for(display_path), cwd))


def _append_conditional_prohibition_notice(read_path: str, display_path: str, notices: list[str]) -> None:
    """適用後の実ファイルを読み、条件付き禁止形の警告があれば通知へ加える。"""
    try:
        content = pathlib.Path(read_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return
    warnings = _check_conditional_prohibition(pathlib.Path(display_path), content)
    if warnings:
        notices.append(_llm_notice("\n".join(warnings), tag="warn"))


def _plan_main_path_for(display_path: str) -> str:
    """計画構成要素のパスから対応する計画ファイル（メイン）の絶対パスを返す。

    計画ファイル（詳細）`<stem>.detail.md`はstem導出で計画ファイル（メイン）`<stem>.md`へ変換する。
    計画ファイル（メイン）の節構成検査が計画ファイル（詳細）の実在・節構成も検査するため、
    計画ファイル（詳細）書込み時も計画ファイル（メイン）パスを検査案内の対象とする。
    計画ファイル（メイン）パスはそのまま返す。
    """
    if display_path.endswith(".detail.md"):
        return display_path[: -len(".detail.md")] + ".md"
    return display_path


def _plan_file_check_notice(file_path: str, cwd: str) -> str:
    """計画ファイル全文書き込み後に実行する計画構造検査の案内文を返す。"""
    check_script = pathlib.Path(__file__).resolve().parents[1] / "skills/plan-mode/scripts/check_plan_file.py"
    work_dir_option = f" --work-dir {shlex.quote(cwd)}" if cwd else ""
    return _llm_notice(
        f"plan file {file_path} was written. Run the post-write checks:"
        f" `uv run --script {shlex.quote(str(check_script))}{work_dir_option}"
        f" {shlex.quote(file_path)}`."
        " Replace --work-dir if the plan targets a repository other than the session's"
        " working directory.",
        tag="notice",
    )


def _handle_bash_tool(session_id: str, command: str, cwd: str) -> None:
    """成功したBashコマンドから検証・git状態を更新する。"""
    command = _strip_command_prefixes(command)
    git_events = extract_git_events(command, cwd)

    def _apply_bash_updates(state: dict) -> dict | None:
        changed = False
        if not state.get("test_executed", False) and any(pattern.search(command) for pattern in _TEST_PATTERNS):
            state["test_executed"] = True
            changed = True
        log_state = state.get("git_log_checked")
        log_modified = False
        for event in git_events:
            if not event.cwd_resolved:
                continue
            if event.subcommand == "log":
                if event.cwd:
                    if not isinstance(log_state, dict):
                        log_state = {}
                    if not log_state.get(event.cwd, False):
                        log_state[event.cwd] = True
                        log_modified = True
            elif event.subcommand in _GIT_LOG_RESET_SUBCOMMANDS:
                if isinstance(log_state, dict):
                    if event.cwd and event.cwd in log_state:
                        del log_state[event.cwd]
                        log_modified = True
                elif log_state:
                    log_state = False
                    log_modified = True
        if log_modified:
            state["git_log_checked"] = log_state
            changed = True
        for event in git_events:
            if not event.cwd_resolved:
                continue
            if event.subcommand == "commit" and _git_commit_is_amend_or_fixup(event.subcommand_args):
                changed = _set_amend_pending_status_check(state, event.cwd) is not None or changed
            elif (
                event.subcommand == "push"
                and _git_status.git_push_is_real_send(event.subcommand_args)
                and _reset_amend_pending_status_check(state, event.cwd) is not None
            ):
                changed = True
        return state if changed else None

    update_state(session_id, _apply_bash_updates)


def _dispatch(payload_text: str, notices: list[str]) -> int:
    """payloadを解析し、通知本文を`notices`へ蓄積する。終了コードは常に0。"""
    parsed = _parse_hook_payload(payload_text)
    if parsed is None:
        return 0
    payload, session_id, tool_name, tool_input, cwd = parsed

    # 対象リポジトリで新たに回答されたTBDファイルがある場合に通知する。
    # ツール種別に依らず検査し、ユーザーの回答から通知までの遅延を抑える。
    if cwd:
        tbd_notice = _uwi_completion.build_notice(session_id, cwd, resolve_hook_agent_id(payload))
        if tbd_notice is not None:
            notices.append(_llm_notice(tbd_notice, tag="notice"))

    # pyfltr MCPのrun_for_agentはPostToolUseへ到達した時点で成功済みである。
    # CLI経由と同じ検証完了契約として記録し、コミット前の未検証警告を抑制する。
    if tool_name == _PYFLTR_RUN_FOR_AGENT_TOOL_NAME:
        _record_test_executed(session_id)
        return 0

    # Skill: plan-modeスキル呼び出し検出とprocess-feedbacks起動検出
    if tool_name == "Skill":
        _record_skill_use(session_id, tool_input.get("skill"))
        return 0

    # AgentとTask: 後続の分岐が対象としないツールのため、記録せずに終了する
    if tool_name in ("Agent", "Task"):
        return 0

    # agents_server応答からsession_id→cwdを保存し、session状態を更新する。
    if tool_name in _AGENTS_SERVER_TOOL_NAMES:
        tool_response = payload.get("tool_response", {})
        structured = _extract_agents_server_structured_response(tool_response)
        moved_to_background = _stop_gate.background_task_id_from_notice(tool_response) is not None
        operation = tool_name.rsplit("__", 1)[-1]
        if tool_name in _AGENTS_SERVER_DIAGNOSTIC_TOOLS and not moved_to_background:
            missing = _agents_server_missing_response_fields(session_id, payload, structured, tool_name)
            if missing:
                display_name = tool_name.rsplit("__", 1)[-1]
                notices.append(_llm_notice(f"warn: {display_name} response is missing or invalid {', '.join(missing)}."))
        remote_session_id = structured.get("session_id")
        if moved_to_background and not (isinstance(remote_session_id, str) and remote_session_id):
            _record_agents_server_observation_attempt(session_id, tool_input, operation=operation)
            return 0
        cwd_value = _agents_server_recorded_cwd(session_id, payload, structured, tool_name)
        owner_agent_id = resolve_hook_agent_id(payload)
        if tool_name in _AGENTS_SERVER_START_TOOLS:
            model_type = structured.get("model_type")
            model_type = model_type if isinstance(model_type, str) else None
            if model_type in _TRACKED_MODEL_TYPES:
                _process_loop_log.append("subagent_start", type=model_type)
            _record_agents_server_session_state(
                session_id,
                structured,
                operation=operation,
                owner_agent_id=owner_agent_id,
                cwd=cwd_value if isinstance(cwd_value, str) else None,
                model_type=model_type,
            )
        else:
            if operation in {"wait", "kill"}:
                _log_tracked_session_end(session_id, structured)
            _record_agents_server_session_state(
                session_id,
                structured,
                operation=operation,
                owner_agent_id=owner_agent_id,
            )
        return 0

    # Readは本フックで状態更新を行わない。
    if tool_name == "Read":
        return 0

    # Write / Edit / MultiEdit: ファイル編集は対象コミットの親子関係を変えないため
    # git_log_checkedをリセットしない（リセット対象は`_GIT_LOG_RESET_SUBCOMMANDS`が定める
    # commit / rebase / resetのみとする）。
    if tool_name in ("Write", "Edit", "MultiEdit", _hook_tool_input.CODEX_APPLY_PATCH_TOOL):
        _handle_edit_tool(session_id, tool_name, tool_input, cwd, notices)
        return 0

    # Bash以外はここで終了
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0

    if tool_input.get("run_in_background"):
        task_id = _background_task_id_from_response(payload.get("tool_response"))
        if task_id is not None:
            _record_background_task_id(session_id, task_id)

    _handle_bash_tool(session_id, command, cwd)
    return 0


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0。

    フック応答はstdout全体を1つのJSONとして解析されるため、蓄積した通知本文を
    改行で連結して1回だけ出力する。分岐ごとの出力は複数JSONの生成につながる。
    """
    notices: list[str] = []
    exit_code = _dispatch(payload_text, notices)
    if notices:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": "\n".join(notices),
                    }
                },
                ensure_ascii=False,
            )
        )
    return exit_code
