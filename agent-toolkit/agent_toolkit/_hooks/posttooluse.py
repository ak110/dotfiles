r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録とplan file形式検査。

Bash / Write / Edit / MultiEdit / apply_patch / Skill / Read / Agent / Taskの実行後に
イベントを検出し、セッション状態ファイルに記録する。
PreToolUseやStopフックが参照して警告・提案の判定に使う。
本モジュールは実行後の観測と警告だけを行い、遮断経路を持たない。
除去可能な警告が反復しても、完了済みのツール操作を遡って遮断しない。

編集入力は`_hook_tool_input`が共通の操作記録へ正規化する。
Codexでは成功した`apply_patch`だけが本フックへ届く。Bashは終了コードを取得できないため、
`git log`確認・amend・push・検証実行の成功状態を記録しない。

検出対象:

1. テスト実行 (Bash / pyfltr MCPの`run`)
2. git log確認状態の記録・リセット (Bash: logで記録、対象コミットの親子関係が
   変化する操作＝commit/rebase/resetでリセット)
3. plan file（計画作業root `~/.claude/plans/` または
   保存済み計画root `$(atk config get private_notes)/plans/` 配下）形式検査 (Write / Edit / MultiEdit / apply_patch)
4. plan-modeスキル呼び出し検出 (Skill)
5. 計画実行系`model_type`の`agents_server` sessionの起動時刻と終了時刻の`_process_loop_log`記録
6. agents_server MCP呼び出しと`atk agents wait`実行後のsession状態記録、開始・再開したsessionの待機対象登録
7. exit-session起動検知による`autonomous_exit_invoked`の記録と
   `process_wi_skill_invoked`のリセット (Skill)
8. 現在の計画ファイルパス記録 (Write / Edit / MultiEdit、plan file判定時)
   （pretooluse.py側の遡及スキャン記録検査が計画ファイル本文を再読み込みする際に使用）
9. 編集ファイルパス蓄積（Write / Edit / MultiEdit、`session_edited_files`リストへ追記）
   （pretooluse.py側の一括ステージ警告で自セッション編集対象の判定に使用）
10. `git commit --amend` / `git commit --fixup` 成功時のcwd別
    `amend_pending_status_check`フラグ設定（pretooluse.py側の`git push`前dirty検査で参照）
11. `git push`（`--dry-run` / `-n`以外）成功時の該当cwd`amend_pending_status_check`フラグ解除
12. PostToolUseFailure: Bashの同一終了コードの連続失敗だけを記録。非エラーの真偽判定を終了コードで
    表現する公開契約を持つコマンドの終了は記録の対象から外す。その他は状態を変更せず終了
13. PermissionDenied: 状態を変更せず終了
14. 対象リポジトリで新たに回答されたUWIファイルの通知（全ツール共通）
15. 当該セッションで作成又は編集した計画ファイル（メイン）の絶対パス蓄積
    （編集ツールの操作記録と`create_plan_files.py`又は`atk run-script plan-create`のBash標準出力）
"""

import json
import os
import pathlib
import re
import shlex

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._agents_server import (
    state as _agents_server_state,
)  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._agents_server import (
    status_file as _agents_server_status_file,
)  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._agents_server import (
    tool_names as _agents_server_tool_names,
)  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._atk.wi import (
    process_loop_log as _process_loop_log,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._git import status as _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._hooks import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    background_task_outputs as _background_task_outputs,
)
from agent_toolkit._hooks import stop_gate as _stop_gate  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._hooks import (
    tool_input as _hook_tool_input,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    uwi_completion as _uwi_completion,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks.agent_id import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_main_agent_context,
    resolve_hook_agent_id,
)
from agent_toolkit._hooks.bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    ExecutionSegment,
    extract_execution_segments,
    extract_git_events,
    without_shell_redirections,
)
from agent_toolkit._hooks.notice import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _WARN_TAG,
    set_warning_session_id,
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    read_state,
    record_atk_help_paths,
    record_bash_failure,
    reset_bash_failure_sequence,
    update_state,
)
from agent_toolkit._hooks.task_stop_state import consume_completion, target_ids  # noqa: E402

# pylint: disable=wrong-import-position,import-error
from agent_toolkit._hooks.tracked_model_types import TRACKED_MODEL_TYPES as _TRACKED_MODEL_TYPES  # noqa: E402
from agent_toolkit._plan.locations import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_component_file,
    is_plan_main_file,
)

# pylint: enable=wrong-import-position,import-error

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/posttooluse"

# agent-toolkitプラグインに同梱するpyfltr MCPの検証実行ツール名。
# hooks/hooks.jsonのPostToolUse matcherと同一値を保つ。
# 本値はpyfltrが公開するMCPツール名と完全一致でなければ当該検査が発動しない。
# MCPサーバーは`.mcp.json`が指定する版の下限だけを持ち、実際に解決される版は実行環境の
# uvxのキャッシュが決めるため、pyfltr側の改名から当該キャッシュが更新されるまでの間は
# 当該検査が発動しない期間が生じる（改名時の実測では、最新版が3.18.0の時点で
# `uvx --from "pyfltr>=3.17.8" pyfltr --version`が3.17.10を返した）。
_PYFLTR_RUN_TOOL_NAME = "mcp__plugin_agent-toolkit_pyfltr__run"

_llm_notice = _notice_formatter(_HOOK_ID)


# --- Bashコマンド前処理 ---

# コマンド先頭またはセグメント区切り（`;`・`&`・`|`）直後に並ぶ接頭辞を捕捉する。
# 対象は`KEY=VALUE`の環境変数代入と`timeout <時間>`の時間制限で、両者の混在と連続も1回で除去する。
# `.sub`で接頭辞列を除去し、先頭の区切り文字＋空白は維持する。
_COMMAND_PREFIX_PATTERN = re.compile(r"(\A|[;&|])(\s*)(?:[A-Za-z_]\w*=\S*\s+|timeout\s+\d+(?:\.\d+)?[smhd]?\s+)+")


def _strip_command_prefixes(command: str) -> str:
    """コマンド先頭・セグメント区切り直後の環境変数代入と時間制限の接頭辞を除去する。

    用途: git操作検出が`timeout 600 git log ...`のような接頭辞付きコマンドのサブコマンドを取得できず、
    確認済みでも未確認として警告される問題に追従する。
    実行位置のトークン列を入力とする検査（検証コマンドの検出など）は同じ接頭辞を自ら解決するため、
    本関数の適用結果に依存しない。
    適用範囲: Bashコマンド文字列。`KEY=VALUE`と`timeout <時間>`の単純形式のみを対象とし、
    クォート内に空白を含む値・`env`コマンド経由・行継続バックスラッシュ・
    `timeout`のオプション付き形式（`-k 10s 600`等、引数の境界を字句だけで確定できない）は対象外とする。
    """
    return _COMMAND_PREFIX_PATTERN.sub(r"\1\2", command)


# --- テスト実行検出 ---

_PYFLTR_VERIFY_SUBCOMMAND_HEADS: frozenset[str] = frozenset({"run", "ci", "fast", "agent"})
"""検証を実行する`pyfltr`のサブコマンドの先頭語。

`run-for-agent`のようなハイフン区切りのサブコマンドを含めるため、先頭語で照合する。
`list-runs`・`show-run`のように実行済みrunを参照するだけのサブコマンドは先頭語が一致せず対象外となる。
"""

_PRECOMMIT_EXECUTABLES: frozenset[str] = frozenset({"pre-commit", "prek"})

_TASK_RUNNERS_WITHOUT_SUBCOMMAND: frozenset[str] = frozenset({"make", "just", "task"})
"""実行ファイル名の直後にアクション名を取るタスクランナー。"""

_TASK_RUNNERS_WITH_OPTIONAL_RUN: frozenset[str] = frozenset({"npm", "pnpm", "yarn"})
"""アクション名の前の`run`を省略できるタスクランナー。"""

_TASK_RUNNER_VERIFY_KEYWORDS: tuple[str, ...] = ("test", "check", "validate")
"""タスクランナーのアクション名を検証の実行と判定する語。

`ci-local-check`・`test-browser`・`e2etest`のような複合名を検出するため、アクション名との完全一致ではなく
部分一致で照合する。検出漏れは検証済みのcommitへ誤った警告を返す側の誤りであり、
本判定では当該方向の誤りを優先して避ける。
"""


def _executable_name(token: str) -> str:
    """実行トークンからディレクトリ部分を除いた実行ファイル名を返す。"""
    return token.replace("\\", "/").rsplit("/", 1)[-1]


def _operand_index(tokens: tuple[str, ...], start: int) -> int | None:
    """`start`以降で最初にオプションでないトークンの添字を返す。無い場合はNoneを返す。"""
    for index in range(start, len(tokens)):
        if not tokens[index].startswith("-"):
            return index
    return None


def _is_verification_invocation(tokens: tuple[str, ...]) -> bool:
    """実行位置のトークン列が検証コマンドの起動であるかを返す。

    入力は`resolve_execution_segment`が`uv run`・`uvx`・`timeout`・環境変数代入などの実行前置語を
    解決した後のトークン列とする。前置語のオプションの部分集合を列挙した文字列一致で判定しないため、
    `uv run --no-sync pyfltr run-for-agent`のように列挙外のオプションを伴う正規の起動も検出する。
    `uv run --with pytest python -c ...`のようにオプションの値へ検証コマンド名が現れる形は、
    実行位置が`python`に解決されるため検出しない。
    """
    if not tokens:
        return False
    executable = _executable_name(tokens[0])
    if executable == "pytest":
        return True
    operand = _operand_index(tokens, 1)
    if executable == "pyfltr":
        return operand is not None and tokens[operand].split("-", 1)[0] in _PYFLTR_VERIFY_SUBCOMMAND_HEADS
    if executable in _PRECOMMIT_EXECUTABLES:
        return operand is not None and tokens[operand] == "run"
    if executable == "cargo":
        return operand is not None and tokens[operand] == "test"
    return _is_task_runner_verification(executable, tokens, operand)


def _is_task_runner_verification(executable: str, tokens: tuple[str, ...], operand: int | None) -> bool:
    """タスクランナー経由の検証アクションの起動であるかを返す。"""
    if executable in _TASK_RUNNERS_WITH_OPTIONAL_RUN:
        if operand is not None and tokens[operand] == "run":
            operand = _operand_index(tokens, operand + 1)
    elif executable == "mise":
        if operand is None or tokens[operand] != "run":
            return False
        operand = _operand_index(tokens, operand + 1)
    elif executable not in _TASK_RUNNERS_WITHOUT_SUBCOMMAND:
        return False
    if operand is None:
        return False
    return any(keyword in tokens[operand] for keyword in _TASK_RUNNER_VERIFY_KEYWORDS)


def _has_verification_invocation(command: str) -> bool:
    """Bashコマンドの実行位置のいずれかが検証コマンドの起動であるかを返す。"""
    return any(
        segment.resolved and _is_verification_invocation(segment.tokens) for segment in extract_execution_segments(command)
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

# process-wiスキル呼び出し検出。フルネームとスラッシュコマンド短縮名の両方を許容する。
_PROCESS_WI_SKILL_NAMES = frozenset({"agent-toolkit:process-wi", "process-wi"})

_AUTONOMOUS_EXIT_STATE_KEY = "autonomous_exit_invoked"

# Claude CodeとCodexが生成するagents_serverの完全修飾MCP tool名。
_AGENTS_SERVER_NAMESPACES = _agents_server_tool_names.MCP_NAMESPACES
_AGENTS_SERVER_START_OPERATIONS = frozenset(("start", "start_custom", "start_explore", "start_write", "start_shell"))
_AGENTS_SERVER_START_TOOLS = frozenset(
    f"{namespace}{tool}" for namespace in _AGENTS_SERVER_NAMESPACES for tool in _AGENTS_SERVER_START_OPERATIONS
)
_AGENTS_SERVER_SEND_TOOLS = frozenset(f"{namespace}send_message" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_KILL_TOOLS = frozenset(f"{namespace}kill" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_STOP_TOOLS = frozenset(f"{namespace}stop" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_LIST_TOOLS = frozenset(f"{namespace}list" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_TOOL_NAMES = (
    _AGENTS_SERVER_START_TOOLS | _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS | _AGENTS_SERVER_STOP_TOOLS
)
_AGENTS_SERVER_DIAGNOSTIC_TOOLS = _AGENTS_SERVER_TOOL_NAMES | _AGENTS_SERVER_LIST_TOOLS
# `show`は稼働中の子sessionの識別子と`cwd`の対を返すため、当該対の記録だけを目的として受信する。
_AGENTS_SERVER_SHOW_TOOLS = frozenset(f"{namespace}show" for namespace in _AGENTS_SERVER_NAMESPACES)

# hooks.json・hooks.codex.jsonのPostToolUse matcherが被覆すべきagents_serverツール名の全体。
# 一致検査（posttooluse_test.py）が実装側の集合として参照するため、下線接頭辞を付けない。
AGENTS_SERVER_HOOK_TOOL_NAMES = _AGENTS_SERVER_TOOL_NAMES | _AGENTS_SERVER_SHOW_TOOLS | _AGENTS_SERVER_LIST_TOOLS

_AGENTS_SERVER_SESSION_CWD_KEY = "agents_server_cwd_by_session"
_AGENTS_SERVER_SESSION_STATE_KEY = "agents_server_sessions"


# --- plan file形式検査の定数 ---


def _set_process_wi_invoked(state: dict) -> dict | None:
    """process-wiスキル起動フラグを常時Trueへ上書きする。

    新規process-wiラン開始時に前ランの残置フラグを無視して確実にTrueへ強制上書きするため冪等スキップを廃止する。
    リセット経路は`_reset_process_wi_invoked`（exit-session起動検知）と併用する。
    """
    state["process_wi_skill_invoked"] = True
    return state


def _reset_process_wi_invoked(state: dict) -> dict | None:
    """`process_wi_skill_invoked`を偽へ戻す。既に偽ならNoneを返す（冪等）。"""
    if not state.get("process_wi_skill_invoked", False):
        return None
    state["process_wi_skill_invoked"] = False
    return state


def _record_exit_session_invoked(state: dict) -> dict | None:
    """exit-session呼び出しを記録し、`process_wi_skill_invoked`をリセットする。"""
    changed = False
    if state.get(_AUTONOMOUS_EXIT_STATE_KEY) is not True:
        state[_AUTONOMOUS_EXIT_STATE_KEY] = True
        changed = True
    if _reset_process_wi_invoked(state) is not None:
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


def _agents_server_remote_session_id(tool_input: object, structured: dict, tool_name: str) -> str | None:
    """操作ごとの正本から委譲先session識別子を返す。"""
    source = structured if tool_name in _AGENTS_SERVER_START_TOOLS else tool_input
    value = source.get("session_id") if isinstance(source, dict) else None
    return value if isinstance(value, str) and value else None


def _agents_server_recorded_cwd(session_id: str, payload: dict, structured: dict, tool_name: str) -> object:
    """startの入力cwdまたはsessionごとのcwd mapから応答のcwd候補を取得する。"""
    tool_input = payload.get("tool_input")
    if tool_name in _AGENTS_SERVER_START_TOOLS:
        input_cwd = tool_input.get("cwd") if isinstance(tool_input, dict) else None
        return input_cwd if _is_nonempty_absolute_cwd(input_cwd) else None
    state = read_state(session_id)
    remote_session_id = _agents_server_remote_session_id(tool_input, structured, tool_name)
    cwd_map = state.get(_AGENTS_SERVER_SESSION_CWD_KEY)
    return cwd_map.get(remote_session_id) if isinstance(cwd_map, dict) else None


def _agents_server_model_type(tool_input: dict, operation: str) -> str | None:
    """開始操作の入力から工程別モデル設定の種別を返す。"""
    if operation == "start":
        task_path = tool_input.get("subagent_md_path")
        return (
            _agents_server_state.TASK_MODEL_TYPES.get(pathlib.PurePath(task_path).name) if isinstance(task_path, str) else None
        )
    if operation == "start_custom":
        model_type = tool_input.get("model_type")
        return model_type if isinstance(model_type, str) else None
    if operation == "start_explore":
        return "explore_fast" if tool_input.get("fast", True) else "explore"
    if operation in {"start_write", "start_shell"}:
        return "explore_fast"
    return None


def _agents_server_missing_response_fields(session_id: str, payload: dict, structured: dict, tool_name: str) -> list[str]:
    """成功した応答から状態記録に必要な欠落項目を列挙する。"""
    operation = tool_name.rsplit("__", 1)[-1]
    if operation == "stop":
        return []
    missing: list[str] = []
    if not structured:
        missing.append("response")
    required_fields: tuple[str, ...]
    if tool_name in _AGENTS_SERVER_START_TOOLS:
        required_fields = ("session_id", "status", "root_session_id")
    elif tool_name in _AGENTS_SERVER_LIST_TOOLS:
        required_fields = ("root_session_id",)
    elif operation in {"wait", "kill"}:
        required_fields = ("status",)
    elif operation == "send_message":
        required_fields = ("delivery",)
    else:
        required_fields = ()
    for field in required_fields:
        value = structured.get(field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or (field == "root_session_id" and not _agents_server_status_file.valid_session_id(value))
        ):
            missing.append(field)
    if tool_name in _AGENTS_SERVER_START_TOOLS and not _is_nonempty_absolute_cwd(
        _agents_server_recorded_cwd(session_id, payload, structured, tool_name)
    ):
        missing.append("cwd")
    return missing


def _live_child_session_cwds(structured: dict) -> list[tuple[str, str]]:
    """agents_serverの応答が返す稼働中の子sessionの識別子と`cwd`の対を取り出す。"""
    entries = structured.get("live_child_sessions")
    if not isinstance(entries, list):
        return []
    pairs: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        child_session_id = entry.get("session_id")
        child_cwd = entry.get("cwd")
        if isinstance(child_session_id, str) and child_session_id and isinstance(child_cwd, str) and child_cwd:
            pairs.append((child_session_id, child_cwd))
    return pairs


def _record_child_session_cwds(session_id: str, structured: dict) -> None:
    """応答が返した稼働中の子sessionの識別子と`cwd`の対を、続行判定が読むキーへ記録する。"""
    pairs = _live_child_session_cwds(structured)
    if not pairs:
        return

    def _mutator(state: dict) -> dict | None:
        cwd_map = state.setdefault(_AGENTS_SERVER_SESSION_CWD_KEY, {})
        changed = False
        for child_session_id, child_cwd in pairs:
            if cwd_map.get(child_session_id) != child_cwd:
                cwd_map[child_session_id] = child_cwd
                changed = True
        return state if changed else None

    update_state(session_id, _mutator)


def _record_agents_server_root_alias(session_id: str, structured: dict) -> None:
    """MCP応答が明示した所有rootを現行会話の別名索引へ記録する。"""
    if os.environ.get("AGENT_TOOLKIT_OWNER_SESSION"):
        return
    root_session_id = structured.get("root_session_id")
    if not _agents_server_status_file.valid_session_id(session_id):
        return
    if not isinstance(root_session_id, str) or not _agents_server_status_file.valid_session_id(root_session_id):
        return
    _agents_server_status_file.write_root_alias(session_id, root_session_id)


def _record_agents_server_session_state(
    session_id: str,
    structured: dict,
    *,
    operation: str,
    owner_agent_id: str,
    cwd: str | None = None,
    model_type: str | None = None,
    remote_session_id: str | None = None,
    fallback_status_host_session: str | None = None,
) -> str | None:
    """agents_serverの公開応答をhook側の状態へ記録する。"""
    if remote_session_id is None:
        value = structured.get("session_id")
        remote_session_id = value if isinstance(value, str) and value else None
    if remote_session_id is None:
        return None

    def _mutator(state: dict) -> dict | None:
        sessions = state.setdefault(_AGENTS_SERVER_SESSION_STATE_KEY, {})
        previous = sessions.get(remote_session_id)
        previous = previous if isinstance(previous, dict) else {}
        status = structured.get("status")
        if operation == "send_message":
            delivery = structured.get("delivery")
            status = "running" if delivery in {"reply_started", "reply_ambiguous"} else previous.get("status")
        if not isinstance(status, str):
            return None
        record = dict(previous)
        record.pop("cwd", None)
        record.pop("_".join(("result", "retrieved")), None)
        record.update({"session_id": remote_session_id, "status": status})
        if model_type is not None:
            record["model_type"] = model_type
        if operation in _AGENTS_SERVER_START_OPERATIONS:
            record["pending_observation"] = True
            record["owner_agent_id"] = owner_agent_id
        elif operation == "send_message":
            delivery = structured.get("delivery")
            # steerはturn_seqを変えず、当該turnの終端を既存の観測が待つ。
            if delivery in {"reply_started", "reply_ambiguous"}:
                record["pending_observation"] = True
                record["owner_agent_id"] = owner_agent_id
        elif operation == "kill":
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
        cwd_map = state.setdefault(_AGENTS_SERVER_SESSION_CWD_KEY, {})
        if isinstance(cwd, str) and cwd and cwd_map.get(remote_session_id) != cwd:
            cwd_map[remote_session_id] = cwd
            changed = True
        # 応答が返した稼働中の子sessionも、識別子と同じ経路で`cwd`を記録する。
        # 返却する識別子の集合と、追送・打ち切りの許可判定の入力の集合を一致させるためである。
        for child_session_id, child_cwd in _live_child_session_cwds(structured):
            if cwd_map.get(child_session_id) != child_cwd:
                cwd_map[child_session_id] = child_cwd
                changed = True
        return state if changed else None

    update_state(session_id, _mutator)
    starts_reply = operation == "send_message" and structured.get("delivery") in {"reply_started", "reply_ambiguous"}
    if operation in _AGENTS_SERVER_START_OPERATIONS or starts_reply:
        try:
            identity = _agents_server_status_file.resolve_status_owner_identity(os.environ)
            if (
                identity is None
                and os.environ.get("AGENT_TOOLKIT_OWNER_SESSION")
                and fallback_status_host_session is not None
                and _agents_server_status_file.valid_session_id(fallback_status_host_session)
            ):
                environment = dict(os.environ)
                environment["AGENT_TOOLKIT_STATUS_HOST_SESSION"] = fallback_status_host_session
                identity = _agents_server_status_file.resolve_status_owner_identity(environment)
        except ValueError as error:
            return f"agents_serverの待機対象を登録できない: {error}"
        if identity is not None and _agents_server_status_file.valid_session_id(remote_session_id):
            _agents_server_status_file.retain_wait_targets(
                identity.root_session_id,
                identity.file_name,
                [remote_session_id],
            )
    return None


def _remove_agents_server_session_record(session_id: str, remote_session_id: str | None) -> None:
    """破棄したsessionの記録を状態キーから除去する。

    `stop`は実行中turnを持つsessionと非終端のsessionを拒否するため、その成功応答は
    当該sessionが終端済み、期限切れ又は既破棄のいずれかであることを含意する。
    """
    if remote_session_id is None:
        return

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
        if not isinstance(sessions, dict) or remote_session_id not in sessions:
            return None
        del sessions[remote_session_id]
        return state

    update_state(session_id, _mutator)


def _log_tracked_session_end(session_id: str, structured: dict, remote_session_id: str | None = None) -> None:
    """終端した計画実行系sessionの終了時刻をprocess-loopの観測ログへ記録する。

    `model_type`は`start`応答にだけ現れるため、起動時に保持した記録から取得する。
    """
    if structured.get("status") not in _agents_server_state.TERMINAL_STATUSES:
        return
    if remote_session_id is None:
        value = structured.get("session_id")
        remote_session_id = value if isinstance(value, str) and value else None
    sessions = read_state(session_id).get(_AGENTS_SERVER_SESSION_STATE_KEY)
    record = sessions.get(remote_session_id) if isinstance(sessions, dict) else None
    model_type = record.get("model_type") if isinstance(record, dict) else None
    if model_type in _TRACKED_MODEL_TYPES:
        _process_loop_log.append("subagent_end", type=model_type)


def _clear_agents_server_pending_observation(session_id: str, owner_agent_id: str) -> None:
    """呼出主体が所有する全sessionの観測待ちを解消する。

    待機は対象sessionを入力に持たず、呼出主体が保持するsession全体を観測するため、
    所有者が一致する既存記録の全件を対象とする。記録が無いsessionへ新規の記録は作成しない。
    """

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
        if not isinstance(sessions, dict):
            return None
        changed = False
        for record in sessions.values():
            if (
                isinstance(record, dict)
                and record.get("owner_agent_id") == owner_agent_id
                and record.get("pending_observation") is not False
            ):
                record["pending_observation"] = False
                changed = True
        return state if changed else None

    update_state(session_id, _mutator)


def _record_agents_server_observation_attempt(
    session_id: str,
    tool_input: dict,
    *,
    operation: str,
) -> None:
    """背景タスクへ移った`kill`の移行通知から観測の試みだけを記録する。

    実行環境が呼び出しを背景タスクへ移すと構造化応答が返らないため、応答の`session_id`と
    `status`を入力とする`_record_agents_server_session_state`は何も更新せずに戻る。
    呼び出しの受理をもって観測を試みたものとして扱い、応答境界へ到達しない経路でも
    `pending_observation`を偽にする。`tool_input`の`session_id`で解決した既存記録に限り、
    記録が無いsessionへ新規の記録を作成しない。`status`・`turn_id`・`kill_requested`などの
    公開状態は移行通知から確定できないため更新しない。
    """
    if operation != "kill":
        return
    remote_session_id = tool_input.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        return

    def _mutator(state: dict) -> dict | None:
        sessions = state.get(_AGENTS_SERVER_SESSION_STATE_KEY)
        if not isinstance(sessions, dict):
            return None
        record = sessions.get(remote_session_id)
        if not isinstance(record, dict) or record.get("pending_observation") is False:
            return None
        record["pending_observation"] = False
        return state

    update_state(session_id, _mutator)


def _is_agents_wait_invocation(tokens: tuple[str, ...]) -> bool:
    """実行トークン列が`atk agents wait`の起動であるかを返す。"""
    if len(tokens) < 3:
        return False
    executable = tokens[0].replace("\\", "/")
    return executable.rsplit("/", 1)[-1] in {"atk", "atk.py"} and tokens[1:3] == ("agents", "wait")


def _recognized_atk_command_path(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    """実行トークン列から公開済みの最下層`atk`サブコマンド経路を返す。"""
    if len(tokens) < 2 or pathlib.PurePath(tokens[0]).name not in {"atk", "atk.py"}:
        return None
    from agent_toolkit._atk.help_text import HELP  # pylint: disable=import-outside-toplevel

    arguments = tuple("wi" if index == 0 and value == "mq" else value for index, value in enumerate(tokens[1:]))
    paths = (tuple(key.split()[1:]) for key in HELP if key.startswith("atk "))
    return next(
        (path for path in sorted(paths, key=len, reverse=True) if arguments[: len(path)] == path),
        None,
    )


def _response_texts(value: object) -> list[str]:
    """Bash応答から標準出力相当の文字列を再帰的に抽出する。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for nested in value.values() for text in _response_texts(nested)]
    if isinstance(value, list):
        return [text for nested in value for text in _response_texts(nested)]
    return []


def _response_has_exit_invocation(value: object) -> bool:
    """応答中の単独JSON行に終了CLIの実行証跡がある場合に真を返す。"""
    if isinstance(value, dict) and value.get("exit_session_invoked") is True:
        return True
    for text in _response_texts(value):
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("exit_session_invoked") is True:
                return True
    return False


def _record_bash_response_state(session_id: str, command: str, tool_response: object) -> None:
    """成功したBash応答を使い、ヘルプ観測と終了CLI起動を記録する。"""
    segments = [segment for segment in extract_execution_segments(command) if segment.resolved and segment.tokens]
    help_paths: list[str] = []
    for segment in segments:
        path = _recognized_atk_command_path(segment.tokens)
        arguments = without_shell_redirections(segment.tokens[1:])
        if path is None or "--help" not in arguments:
            continue
        if arguments != (*path, "--help"):
            continue
        normalized = " ".join(path)
        if normalized not in help_paths:
            help_paths.append(normalized)
    if help_paths:
        record_atk_help_paths(session_id, help_paths)
    exit_invoked = any(
        pathlib.PurePath(segment.tokens[0]).name in {"atk", "atk.py"} and segment.tokens[1:] == ("agents-exit-session",)
        for segment in segments
    )
    if exit_invoked and _response_has_exit_invocation(tool_response):
        update_state(session_id, _record_exit_session_invoked)
    _record_created_plan_file(session_id, segments, tool_response)


_PLAN_CREATION_SCRIPT_NAME = "create_plan_files.py"
_PLAN_CREATION_RUN_SCRIPT_PREFIX = ("atk", "run-script", "plan-create")


def _is_plan_creation_invocation(tokens: tuple[str, ...]) -> bool:
    """実行トークン列が旧又は現行の計画ファイル作成入口であるかを返す。"""
    if any(_PLAN_CREATION_SCRIPT_NAME in token for token in tokens):
        return True
    normalized = (_executable_name(tokens[0]), *tokens[1:]) if tokens else ()
    return normalized[: len(_PLAN_CREATION_RUN_SCRIPT_PREFIX)] == _PLAN_CREATION_RUN_SCRIPT_PREFIX


def _record_created_plan_file(session_id: str, segments: list[ExecutionSegment], tool_response: object) -> None:
    """計画ファイル作成入口の標準出力から計画ファイル（メイン）の絶対パスを記録する。

    当該スクリプトは確定したパスを標準出力へ1行ずつ書くため、計画ファイル（メイン）と判定した行だけを抽出する。
    該当が無い場合は記録せず、PostToolUseの応答を変えない。
    """
    if not any(_is_plan_creation_invocation(segment.tokens) for segment in segments):
        return
    for text in _response_texts(tool_response):
        for line in text.splitlines():
            candidate = line.strip()
            if candidate and is_plan_component_file(candidate):
                _record_plan_file(session_id, candidate)
                return


def _record_agents_wait_observation_attempt(session_id: str, command: str, owner_agent_id: str) -> None:
    """成功したBash入力内の`atk agents wait`を観測の試みとして記録する。"""
    if not any(
        segment.resolved and _is_agents_wait_invocation(segment.tokens) for segment in extract_execution_segments(command)
    ):
        return
    _clear_agents_server_pending_observation(session_id, owner_agent_id)


def _parse_hook_payload(payload_text: str) -> tuple[dict, str, str, dict, str, str] | None:
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
    if event_name == "PermissionDenied" or (event_name == "PostToolUseFailure" and tool_name != "Bash"):
        return None
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    return payload, session_id, tool_name, tool_input, cwd, event_name


_BASH_FAILURE_EXIT_CODE_PATTERN = re.compile(r"^Exit code ([0-9]+)$")


_BOOLEAN_EXIT_CODE_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("atk", "wi", "grep"),
    ("atk", "mq", "grep"),
    ("git", "grep"),
    ("grep",),
    ("egrep",),
    ("fgrep",),
    ("rg",),
    ("pyfltr",),
)
"""非エラーの真偽判定を終了コードで表現する公開契約を持つコマンドの実行位置の接頭語。

集合へ加える判定の基準は、当該コマンドが該当0件などの非エラーの結果を非0の終了コードで表す契約を
公開するかとする。`atk wi grep`の当該契約は`agent_toolkit/_atk/wi/grep.py`のdocstringが定め、
`grep`系と`rg`は一致0件を終了コード1で表す。
当該コマンドの終了を連続失敗として記録すると、読み取り専用の検索が0件で続いた区間で
直接Bashの遮断が成立する。
"""
_DIFF_COMMAND = "diff"
_DIFF_BOOLEAN_EXIT_CODE_OPTIONS: frozenset[str] = frozenset({"-q", "--quiet", "--brief"})
"""`diff`が差分の有無を終了コードで表す指定。当該指定では差分ありの終了コード1が非エラーの結果である。"""


def _is_boolean_exit_code_command(command: str) -> bool:
    """Bash入力が、非エラーの真偽判定を終了コードで表すコマンド1件だけであるかを返す。

    実行位置が1つであり、当該実行位置が当該契約を持つ場合だけ真を返す。
    複数の実行位置を持つ入力では、どの位置が非0で終了したかを失敗payloadから確定できず、
    当該契約を持たないコマンドの失敗を記録から外し得るためである。
    """
    segments = [segment for segment in extract_execution_segments(command) if segment.resolved and segment.tokens]
    if len(segments) != 1:
        return False
    tokens = segments[0].tokens
    name = _executable_name(tokens[0])
    normalized = (name, *tokens[1:])
    if any(normalized[: len(prefix)] == prefix for prefix in _BOOLEAN_EXIT_CODE_COMMAND_PREFIXES):
        return True
    module = tokens[2] if len(tokens) >= 3 and tokens[1] == "-m" else name
    if module == "pyfltr" or module.startswith("pyfltr."):
        return True
    return name == _DIFF_COMMAND and any(token in _DIFF_BOOLEAN_EXIT_CODE_OPTIONS for token in tokens[1:])


def _bash_failure_exit_code(payload: dict) -> int | None:
    """公式の失敗payloadから分類可能なBash終了コードを返す。"""
    if payload.get("is_interrupt") is True:
        return None
    error = payload.get("error")
    if not isinstance(error, str):
        return None
    lines = error.splitlines()
    first_line = lines[0] if lines else ""
    match = _BASH_FAILURE_EXIT_CODE_PATTERN.fullmatch(first_line)
    return int(match.group(1)) if match is not None else None


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
    実行ホストが背景へ移した通知は`stop_gate.background_task_id_from_notice`が扱う。
    """
    if isinstance(value, str):
        match = _BACKGROUND_TASK_ID_RE.search(value)
        return match.group(1) if match is not None else None
    if isinstance(value, dict):
        structured_id = value.get("backgroundTaskId")
        if isinstance(structured_id, str) and structured_id:
            return structured_id
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
    """自セッションのツール呼び出しが返した背景タスクのIDを記録する。

    PreToolUse(TaskStop)が、停止対象が自セッションの起動した背景タスクかを判定する入力とする。
    記録の契機は、Bashの背景実行が成功した応答、同じ指定で失敗した応答、
    およびツール種別を問わない背景移行通知の3つとする。
    所有の根拠は自身の呼び出しが識別子を返したことであり、当該呼び出しの成否に依存しない。
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


def _record_background_task_output(session_id: str, response: object) -> None:
    """背景タスクIDとホストが示した出力先を同じsession状態へ記録する。"""
    pair = _background_task_outputs.task_output_from_response(response)
    if pair is None:
        return
    task_id, output_path = pair

    def _record(state: dict) -> dict | None:
        recorded = state.get("background_task_output_paths")
        recorded = dict(recorded) if isinstance(recorded, dict) else {}
        if recorded.get(task_id) == output_path:
            return None
        recorded[task_id] = output_path
        state["background_task_output_paths"] = recorded
        return state

    update_state(session_id, _record)


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
    if skill_name in _PROCESS_WI_SKILL_NAMES:
        update_state(session_id, _set_process_wi_invoked)


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
        if plan_mode_invoked and is_plan_component_file(display_path) and operation.is_whole_write:
            notices.append(_plan_file_check_notice(_plan_main_path_for(display_path), cwd))


def _plan_main_path_for(display_path: str) -> str:
    """現行計画の構成要素から計画ファイル（メイン）の絶対パスを返す。"""
    return display_path


def _plan_file_check_notice(file_path: str, cwd: str) -> str:
    """計画ファイル全文書き込み後に実行する計画構造検査の案内文を返す。"""
    project_root = pathlib.Path(__file__).resolve().parents[2]
    check_script = project_root / "skills/plan-mode/scripts/check_plan_file.py"
    work_dir_option = f" --work-dir {shlex.quote(cwd)}" if cwd else ""
    return _llm_notice(
        f"計画ファイル{file_path}へ書き込んだ。書き込み後の検査を実行する:"
        f" `uv run --project {shlex.quote(str(project_root))} --locked --no-default-groups"
        f" {shlex.quote(str(check_script))}{work_dir_option}"
        f" {shlex.quote(file_path)}`."
        "計画の対象リポジトリが当該セッションの作業ディレクトリと異なる場合は、"
        "`--work-dir`を対象リポジトリへ置き換える。",
        tag="notice",
    )


def _handle_bash_tool(
    session_id: str,
    command: str,
    cwd: str,
    *,
    owner_agent_id: str,
    record_success_dependent_state: bool,
) -> None:
    """成功したBashコマンドから検証・git状態を更新する。"""
    command = _strip_command_prefixes(command)
    _record_agents_wait_observation_attempt(session_id, command, owner_agent_id)
    if not record_success_dependent_state:
        return
    git_events = extract_git_events(command, cwd)

    def _apply_bash_updates(state: dict) -> dict | None:
        changed = False
        if not state.get("test_executed", False) and _has_verification_invocation(command):
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
    payload, session_id, tool_name, tool_input, cwd, event_name = parsed
    set_warning_session_id(session_id)

    # 所有の根拠は、自セッションのツール呼び出しの応答が背景タスク識別子を返したことである。
    # 起動の成否は所有の有無を変えないため、背景移行通知はツール種別と成否によらず記録する。
    notice_task_id = _stop_gate.background_task_id_from_notice(payload.get("tool_response"))
    if notice_task_id is not None:
        _record_background_task_id(session_id, notice_task_id)
    if tool_input.get("run_in_background") or notice_task_id is not None:
        _record_background_task_output(session_id, payload.get("tool_response"))

    if event_name == "PostToolUseFailure":
        if tool_input.get("run_in_background"):
            failed_task_id = _background_task_id_from_response(payload.get("tool_response"))
            if failed_task_id is not None:
                _record_background_task_id(session_id, failed_task_id)
        exit_code = _bash_failure_exit_code(payload)
        failed_command = tool_input.get("command")
        # 非エラーの真偽判定を終了コードで表すコマンドの終了は実行の失敗ではないため、
        # 連続失敗の記録対象から外し、連続性も解除する。
        if exit_code is None or (isinstance(failed_command, str) and _is_boolean_exit_code_command(failed_command)):
            reset_bash_failure_sequence(session_id)
        elif record_bash_failure(session_id, exit_code):
            notices.append(
                _llm_notice(
                    f"同じ終了コード{exit_code}でBashが2回連続して失敗した。"
                    "次の直接Bash実行を遮断する。原因調査とコマンド実行はagents_serverのstart_shellへ分離する。"
                    "start_shellが成功すると連続失敗の状態を解除し、その後は直接Bashを再開できる。",
                    tag=_WARN_TAG,
                    removable_cause=False,
                )
            )
        return 0

    # 対象リポジトリで新たに回答されたUWIファイルがある場合に通知する。
    # ツール種別に依らず検査し、ユーザーの回答から通知までの遅延を抑える。
    # 当該通知が指示する反映と依存作業の再開はメインが所有するため、
    # in-processのサブエージェントと`agents_server`の委譲先セッションでは通知を組み立てない。
    if cwd and is_main_agent_context(payload):
        uwi_notice = _uwi_completion.build_notice(session_id, cwd, resolve_hook_agent_id(payload))
        if uwi_notice is not None:
            notices.append(_llm_notice(uwi_notice, tag="notice"))

    # pyfltr MCPのrunはPostToolUseへ到達した時点で成功済みである。
    # CLI経由と同じ検証完了契約として記録し、コミット前の未検証警告を抑制する。
    if tool_name == _PYFLTR_RUN_TOOL_NAME:
        _record_test_executed(session_id)
        return 0

    # Skill: plan-modeスキル呼び出し検出とprocess-wi起動検出
    if tool_name == "Skill":
        _record_skill_use(session_id, tool_input.get("skill"))
        return 0

    # AgentとTask: 後続の分岐が対象としないツールのため、記録せずに終了する
    if tool_name in ("Agent", "Task"):
        return 0

    # showの応答が返す稼働中の子sessionは、識別子と`cwd`の対だけを記録する。
    # session記録そのものは当該sessionを起動した主体が持つため、ここでは更新しない。
    if tool_name in _AGENTS_SERVER_SHOW_TOOLS:
        structured = _extract_agents_server_structured_response(payload.get("tool_response", {}))
        _record_child_session_cwds(session_id, structured)
        return 0

    # listはsession状態を変更せず、応答が明示した所有rootだけを別名索引へ記録する。
    if tool_name in _AGENTS_SERVER_LIST_TOOLS:
        structured = _extract_agents_server_structured_response(payload.get("tool_response", {}))
        missing = _agents_server_missing_response_fields(session_id, payload, structured, tool_name)
        if missing:
            notices.append(_llm_notice(f"warn: listの応答で{', '.join(missing)}が欠落しているか不正である。"))
        _record_agents_server_root_alias(session_id, structured)
        return 0

    # agents_server応答からsession_id→cwdを保存し、session状態を更新する。
    if tool_name in _AGENTS_SERVER_TOOL_NAMES:
        tool_response = payload.get("tool_response", {})
        structured = _extract_agents_server_structured_response(tool_response)
        moved_to_background = notice_task_id is not None
        operation = tool_name.rsplit("__", 1)[-1]
        owner_agent_id = resolve_hook_agent_id(payload)
        if tool_name in _AGENTS_SERVER_DIAGNOSTIC_TOOLS and not moved_to_background:
            missing = _agents_server_missing_response_fields(session_id, payload, structured, tool_name)
            if missing:
                display_name = tool_name.rsplit("__", 1)[-1]
                notices.append(_llm_notice(f"warn: {display_name}の応答で{', '.join(missing)}が欠落しているか不正である。"))
        remote_session_id = _agents_server_remote_session_id(tool_input, structured, tool_name)
        if moved_to_background:
            _record_agents_server_observation_attempt(
                session_id,
                tool_input,
                operation=operation,
            )
            return 0
        cwd_value = _agents_server_recorded_cwd(session_id, payload, structured, tool_name)
        if tool_name in _AGENTS_SERVER_START_TOOLS:
            _record_agents_server_root_alias(session_id, structured)
            model_type = _agents_server_model_type(tool_input, operation)
            if model_type in _TRACKED_MODEL_TYPES:
                _process_loop_log.append("subagent_start", type=model_type)
            warning = _record_agents_server_session_state(
                session_id,
                structured,
                operation=operation,
                owner_agent_id=owner_agent_id,
                cwd=cwd_value if isinstance(cwd_value, str) else None,
                model_type=model_type,
                remote_session_id=remote_session_id,
                fallback_status_host_session=session_id if tool_name.startswith("mcp__agents_server__") else None,
            )
            if warning is not None:
                notices.append(_llm_notice(warning, tag=_WARN_TAG, removable_cause=False))
            if operation == "start_shell":
                reset_bash_failure_sequence(session_id, clear_gate=True)
        elif operation == "stop":
            _remove_agents_server_session_record(session_id, remote_session_id)
        else:
            if operation in {"wait", "kill"}:
                _log_tracked_session_end(session_id, structured, remote_session_id)
            warning = _record_agents_server_session_state(
                session_id,
                structured,
                operation=operation,
                owner_agent_id=owner_agent_id,
                remote_session_id=remote_session_id,
                fallback_status_host_session=session_id if tool_name.startswith("mcp__agents_server__") else None,
            )
            if warning is not None:
                notices.append(_llm_notice(warning, tag=_WARN_TAG, removable_cause=False))
        return 0

    if tool_name == "TaskStop":
        consume_completion(session_id, target_ids(tool_input))
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
    # 直接Bashの成功は、連続失敗の通知が求める是正の完了を示す最も一般的な観測である。
    # 解除の契機を分離実行の成功だけに限ると、原因を除去して直接実行を継続した主体へ、
    # 当該セッションの残余で同じ警告が付き続ける。
    reset_bash_failure_sequence(session_id, clear_gate=True)

    if tool_input.get("run_in_background"):
        task_id = _background_task_id_from_response(payload.get("tool_response"))
        if task_id is not None:
            _record_background_task_id(session_id, task_id)

    turn_id = payload.get("turn_id")
    _handle_bash_tool(
        session_id,
        command,
        cwd,
        owner_agent_id=resolve_hook_agent_id(payload),
        record_success_dependent_state=not (isinstance(turn_id, str) and bool(turn_id)),
    )
    _record_bash_response_state(session_id, command, payload.get("tool_response"))
    return 0


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0。

    フック応答はstdout全体を1つのJSONとして解析されるため、蓄積した通知本文を
    改行で連結して1回だけ出力する。分岐ごとの出力は複数JSONの生成につながる。
    """
    notices: list[str] = []
    exit_code = _dispatch(payload_text, notices)
    if notices:
        try:
            event_name = json.loads(payload_text).get("hook_event_name", "PostToolUse")
        except (json.JSONDecodeError, ValueError, AttributeError):
            event_name = "PostToolUse"
        if event_name not in {"PostToolUse", "PostToolUseFailure"}:
            event_name = "PostToolUse"
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event_name,
                        "additionalContext": "\n".join(notices),
                    }
                },
                ensure_ascii=False,
            )
        )
    return exit_code
