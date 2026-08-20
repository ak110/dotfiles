r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録とplan file形式検査。

Bash / Write / Edit / MultiEdit / apply_patch / Skill / Read / EnterPlanMode / Agent / Taskの実行後に
イベントを検出し、セッション状態ファイルに記録する。
PreToolUseやStopフックが参照して警告・提案の判定に使う。

編集入力は`_hook_tool_input`が共通の操作記録へ正規化する。
Codexでは成功した`apply_patch`だけが本フックへ届き、Bashは終了コードを取得できないため登録しない
（`git log`確認・amend・push・検証実行の成功状態はCodexで記録しない）。

検出対象:

1. テスト実行 (Bash / pyfltr MCPの`run_for_agent`)
2. git log確認状態の記録・リセット (Bash: logで記録、対象コミットの親子関係が
   変化する操作＝commit/rebase/resetでリセット)
3. plan file（`~/.claude/plans/*.md`）形式検査 (Write / Edit / MultiEdit / apply_patch)
4. plan-modeスキル呼び出し検出 (Skill)
5. 振り返りスキル呼び出し検出 (Skill)
   （`session_review_invoked`辞書へ記録）
7. 新規作業区切りでの`session_review_invoked`リセット (EnterPlanMode)
8. `_TRACKED_SUBAGENT_TYPES`対象種別のサブエージェント終了時刻の`_process_loop_log`記録
9. Codex App Server MCP呼び出し後のリモートref変化確認
10. exit-session起動検知による`process_feedbacks_skill_invoked`フラグのリセット (Skill)
11. 現在の計画ファイルパス記録 (Write / Edit / MultiEdit、plan file判定時)
    （pretooluse.py側の遡及スキャン記録検査が計画ファイル本文を再読み込みする際に使用）
12. 編集ファイルパス蓄積（Write / Edit / MultiEdit、`session_edited_files`リストへ追記）
    （pretooluse.py側の一括ステージ警告で自セッション編集対象の判定に使用）
13. `git commit --amend` / `git commit --fixup` 成功時のcwd別
    `amend_pending_status_check`フラグ設定（pretooluse.py側の`git push`前dirty検査で参照）
14. `git push`（`--dry-run` / `-n`以外）成功時の該当cwd`amend_pending_status_check`フラグ解除
15. PostToolUseFailure・PermissionDenied: 原則状態を変更せず終了
    （ただしCodex開始点の内部失敗は最新turn未回収とsnapshotを記録し、入力検証失敗は従来どおり変更しない）
16. 条件付き禁止形（「〜した状態で…しない/禁止」）の警告検出 (Write / Edit / MultiEdit、
    `is_agent_facing_md`が対象と判定するコーディングエージェント向け`.md`編集時)
17. `agent-toolkit:delegation`起動の記録 (Skill)
18. 対象リポジトリで新たに回答されたTBDファイルの通知（全ツール共通）
"""

import json
import pathlib
import re
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills" / "plan-mode" / "scripts"))
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _hook_tool_input  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _process_loop_log  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _tbd_completion  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import extract_git_events  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _hook_notice import formatter as _notice_formatter  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_format import is_agent_facing_md  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable=wrong-import-position,import-error
from _tracked_subagent_types import TRACKED_SUBAGENT_TYPES as _TRACKED_SUBAGENT_TYPES  # noqa: E402
from _transcript_agent_id import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_transcript_agent_id as _extract_transcript_agent_id,
)

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

# Stop hookでの振り返り誘導抑止に使う配布物側の振り返りスキル名。観測したらsession_stateへ記録する。
_SESSION_REVIEW_SKILL_NAMES = frozenset({"agent-toolkit:session-review"})

# process-feedbacksスキル呼び出し検出。フルネームとスラッシュコマンド短縮名の両方を許容する。
# Stop hookの拡張照合カテゴリ有効化判定に使う。
_PROCESS_FEEDBACKS_SKILL_NAMES = frozenset({"agent-toolkit:process-feedbacks", "process-feedbacks"})

# session-reviewを自動起動する起点となる処理スキル。
_PLAN_AND_ADD_FEEDBACK_SKILL_NAMES = frozenset({"agent-toolkit:plan-and-add-feedback", "plan-and-add-feedback"})
_ADD_FEEDBACK_SKILL_NAMES = frozenset({"agent-toolkit:add-feedback", "add-feedback"})

# exit-sessionスキル呼び出し検出。process-feedbacksのフラグリセット経路に使う
# （`agent-toolkit:process-feedbacks`「6. 振り返りと終了」節がexit-sessionで終端する）。
_EXIT_SESSION_SKILL_NAMES = frozenset({"agent-toolkit:exit-session", "exit-session"})

_DELEGATION_SKILL_NAMES = frozenset({"agent-toolkit:delegation", "delegation"})

# Codex App Serverの完全修飾MCP tool名。
_CODEX_APP_SERVER_NAMESPACE = "mcp__plugin_agent-toolkit_codex_app_server__"
_CODEX_APP_SERVER_START_TOOL = f"{_CODEX_APP_SERVER_NAMESPACE}codex_start"
_CODEX_APP_SERVER_REPLY_TOOL = f"{_CODEX_APP_SERVER_NAMESPACE}codex_start_reply"
_CODEX_APP_SERVER_RESULT_TOOL = f"{_CODEX_APP_SERVER_NAMESPACE}codex_result"
_CODEX_APP_SERVER_START_TOOLS = frozenset({_CODEX_APP_SERVER_START_TOOL, _CODEX_APP_SERVER_REPLY_TOOL})
_CODEX_APP_SERVER_TOOL_NAMES = frozenset(
    {
        _CODEX_APP_SERVER_START_TOOL,
        f"{_CODEX_APP_SERVER_NAMESPACE}codex_status",
        f"{_CODEX_APP_SERVER_NAMESPACE}codex_wait",
        _CODEX_APP_SERVER_RESULT_TOOL,
        _CODEX_APP_SERVER_REPLY_TOOL,
    }
)

# codex呼び出し前後のリモート参照スナップショットを記録する状態辞書のキー。
# `pretooluse.py`がtool_use_id単位で書き込み、本スクリプトがcodex_result後に読み取り・削除する共有SSOT。
_CODEX_REMOTE_SNAPSHOT_KEY = "codex_remote_snapshot_by_key"
_CODEX_SESSION_CWD_KEY = "codex_app_server_cwd_by_session"
_CODEX_SESSION_STATE_KEY = "codex_app_server_sessions"


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
    """自動振り返りの起点フラグを偽へ戻す。全て偽ならNoneを返す（冪等）。"""
    keys = (
        "process_feedbacks_skill_invoked",
        "plan_and_add_feedback_skill_invoked",
        "add_feedback_skill_invoked",
    )
    if not any(state.get(key, False) for key in keys):
        return None
    for key in keys:
        state[key] = False
    return state


def _diff_remote_snapshots(
    before: dict[str, dict[str, str] | None],
    after: dict[str, dict[str, str] | None],
) -> set[str]:
    """2つのリモートスナップショット間で参照が変化したリモート名の集合を返す。

    値`None`は当該リモートの`git ls-remote`取得に失敗したことを示すマーカーであり、
    リモート名自体は既知として保持されている（`snapshot_remote_refs`参照）。
    比較対象のいずれかが`None`の場合（取得失敗）は対象から除外する
    （取得失敗を「参照が消えた」または「新規追加された」という差分と誤認しないため）。
    キー自体が存在しない（`before`辞書にキーが無い）リモートのみを「新規追加」と判定し、
    参照を1件以上持つ場合に対象へ含める。
    """
    changed: set[str] = set()
    for remote, before_refs in before.items():
        if remote not in after:
            continue
        after_refs = after[remote]
        if before_refs is None or after_refs is None:
            continue
        if before_refs != after_refs:
            changed.add(remote)
    for remote, after_refs in after.items():
        if remote not in before and after_refs:
            changed.add(remote)
    return changed


def _record_codex_session_cwd(session_id: str, payload: dict) -> None:
    """開始・継続応答のsession_idと開始snapshotのcwdを状態へ保存する。"""
    tool_response = payload.get("tool_response", {})
    structured = tool_response.get("structuredContent") if isinstance(tool_response, dict) else None
    if not isinstance(structured, dict):
        structured = tool_response if isinstance(tool_response, dict) else {}
    remote_session_id = structured.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        return
    snapshot_key = _codex_snapshot_key(payload, session_id)
    tool_input = payload.get("tool_input")
    cwd = tool_input.get("cwd") if isinstance(tool_input, dict) else None
    if not isinstance(cwd, str) or not cwd:
        entries = read_state(session_id).get(_CODEX_REMOTE_SNAPSHOT_KEY)
        recorded = entries.get(snapshot_key) if isinstance(entries, dict) else None
        cwd = recorded.get("cwd") if isinstance(recorded, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return
    _record_codex_session_state(session_id, structured, cwd=cwd, snapshot_key=snapshot_key)


def _codex_snapshot_key(payload: dict, session_id: str) -> str:
    """PreToolUseとPostToolUseで共有するCodex snapshotキーを返す。"""
    tool_use_id = payload.get("tool_use_id")
    if isinstance(tool_use_id, str) and tool_use_id:
        return tool_use_id
    agent_id = _extract_transcript_agent_id(payload.get("transcript_path"))
    return agent_id or f"session:{session_id}"


def _clear_codex_remote_snapshot(payload: dict, session_id: str) -> None:
    """Codex開始点の失敗時に、比較対象にならないsnapshotだけを削除する。"""
    snapshot_key = _codex_snapshot_key(payload, session_id)

    def _clear(state: dict) -> dict | None:
        entries = state.get(_CODEX_REMOTE_SNAPSHOT_KEY)
        if not isinstance(entries, dict) or snapshot_key not in entries:
            return None
        del entries[snapshot_key]
        return state

    update_state(session_id, _clear)


def _codex_failure_message(payload: dict) -> str:
    """PostToolUseFailureの入力量から、検証失敗を判定するための本文を取り出す。"""
    values: list[object] = [payload.get("error"), payload.get("message")]
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict):
        values.extend((tool_response.get("error"), tool_response.get("message")))
    messages: list[str] = []
    for value in values:
        if isinstance(value, str):
            messages.append(value)
        elif isinstance(value, dict) and isinstance(value.get("message"), str):
            messages.append(value["message"])
    return " ".join(messages).lower()


def _codex_result_failure_keeps_snapshot(session_id: str, payload: dict) -> bool:
    """未終端sessionの結果回収失敗時にsnapshotを保持する。"""
    tool_input = payload.get("tool_input")
    remote_session_id = tool_input.get("session_id") if isinstance(tool_input, dict) else None
    state = read_state(session_id)
    sessions = state.get(_CODEX_SESSION_STATE_KEY)
    record = sessions.get(remote_session_id) if isinstance(sessions, dict) else None
    return (
        isinstance(record, dict)
        and record.get("status") in {"running", "completed", "failed", "interrupted"}
        and record.get("result_retrieved") is not True
    )


def _record_codex_reply_failure(payload: dict, session_id: str) -> None:
    """内部失敗だけを最新ターン未回収として記録する。"""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        _clear_codex_remote_snapshot(payload, session_id)
        return
    remote_session_id = tool_input.get("session_id")
    prompt = tool_input.get("prompt")
    failure_message = _codex_failure_message(payload)
    input_failure_fragments = (
        "prompt must",
        "requires a non-empty",
        "session_id must",
        "unknown codex session",
        "previous codex turn is still running",
        "codex_result must be called",
        "cannot retry an ambiguous turn/start failure",
    )
    if any(fragment in failure_message for fragment in input_failure_fragments):
        _clear_codex_remote_snapshot(payload, session_id)
        return
    state = read_state(session_id)
    sessions = state.get(_CODEX_SESSION_STATE_KEY)
    record = sessions.get(remote_session_id) if isinstance(sessions, dict) else None
    terminal_statuses = {"completed", "failed", "interrupted"}
    eligible = (
        isinstance(remote_session_id, str)
        and isinstance(prompt, str)
        and bool(prompt.strip())
        and isinstance(record, dict)
        and record.get("status") in terminal_statuses
        and record.get("result_retrieved") is True
    )
    if not eligible:
        # 入力検証失敗や未回収ターンへの重複呼び出しでは、既存の状態を変更しない。
        _clear_codex_remote_snapshot(payload, session_id)
        return

    snapshot_key = _codex_snapshot_key(payload, session_id)

    def _mark_failed(state: dict) -> dict | None:
        current_sessions = state.get(_CODEX_SESSION_STATE_KEY)
        if not isinstance(current_sessions, dict):
            return None
        current = current_sessions.get(remote_session_id)
        if not isinstance(current, dict):
            return None
        if current.get("status") not in terminal_statuses or current.get("result_retrieved") is not True:
            return None
        current.update(
            {
                "status": "failed",
                "turn_id": "",
                "result_retrieved": False,
                "snapshot_key": snapshot_key,
            }
        )
        current_sessions[remote_session_id] = current
        return state

    update_state(session_id, _mark_failed)


def _record_codex_session_state(
    session_id: str,
    structured: dict,
    *,
    cwd: str | None = None,
    snapshot_key: str | None = None,
) -> None:
    """Codex App Serverの各tool応答をStop判定用状態へ記録する。"""
    remote_session_id = structured.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        return
    turn_id = structured.get("turn_id")
    status = structured.get("status")
    if not isinstance(status, str):
        return

    def _mutator(state: dict) -> dict | None:
        sessions = state.setdefault(_CODEX_SESSION_STATE_KEY, {})
        previous = sessions.get(remote_session_id)
        previous = previous if isinstance(previous, dict) else {}
        record = dict(previous)
        record.update({"session_id": remote_session_id, "status": status})
        if isinstance(turn_id, str) and turn_id:
            record["turn_id"] = turn_id
        if isinstance(cwd, str) and cwd:
            record["cwd"] = cwd
        if isinstance(snapshot_key, str) and snapshot_key:
            record["snapshot_key"] = snapshot_key
        if structured.get("status") in {"running"}:
            record["result_retrieved"] = False
        if structured.get("status") in {"completed", "failed", "interrupted"} and structured.get("error") is not None:
            record["error"] = structured.get("error")
        if structured.get("agent_message") is not None:
            record["agent_message"] = structured.get("agent_message")
        if structured.get("status") in {"completed", "failed", "interrupted"}:
            # codex_resultだけが回収済みを表す。result tool以外のterminal観測はFalseを保持する。
            record["result_retrieved"] = bool(record.get("result_retrieved", False))
        if sessions.get(remote_session_id) == record:
            return None
        sessions[remote_session_id] = record
        cwd_map = state.setdefault(_CODEX_SESSION_CWD_KEY, {})
        if isinstance(cwd, str) and cwd and cwd_map.get(remote_session_id) != cwd:
            cwd_map[remote_session_id] = cwd
        return state

    update_state(session_id, _mutator)


def _warn_codex_remote_change(session_id: str, payload: dict) -> str | None:
    """codex_result受領後に開始時点との差分を比較し、必要なら警告本文を返す。"""
    state = read_state(session_id)
    entries = state.get(_CODEX_REMOTE_SNAPSHOT_KEY)

    tool_response = payload.get("tool_response", {})
    structured = tool_response.get("structuredContent") if isinstance(tool_response, dict) else None
    if not isinstance(structured, dict):
        structured = tool_response if isinstance(tool_response, dict) else {}
    remote_session_id = structured.get("session_id")
    sessions = state.get(_CODEX_SESSION_STATE_KEY)
    session_record = sessions.get(remote_session_id) if isinstance(sessions, dict) else None
    key = session_record.get("snapshot_key") if isinstance(session_record, dict) else None
    if not isinstance(key, str) or not key:
        key = _codex_snapshot_key(payload, session_id)
    recorded = entries.get(key) if isinstance(entries, dict) else None
    cwd = recorded.get("cwd") if isinstance(recorded, dict) else None
    if isinstance(remote_session_id, str) and remote_session_id and isinstance(cwd, str) and cwd:

        def _record_session_cwd(state: dict) -> dict | None:
            cwd_map = state.setdefault(_CODEX_SESSION_CWD_KEY, {})
            if cwd_map.get(remote_session_id) == cwd:
                return None
            cwd_map[remote_session_id] = cwd
            return state

        update_state(session_id, _record_session_cwd)

    def _clear(state: dict) -> dict | None:
        changed = False
        snapshot_entries = state.get(_CODEX_REMOTE_SNAPSHOT_KEY)
        if isinstance(snapshot_entries, dict) and key in snapshot_entries:
            del snapshot_entries[key]
            changed = True
        session_entries = state.get(_CODEX_SESSION_STATE_KEY)
        if isinstance(session_entries, dict) and isinstance(remote_session_id, str):
            current = session_entries.get(remote_session_id)
            if isinstance(current, dict) and current.get("snapshot_key") == key:
                current.pop("snapshot_key", None)
                session_entries[remote_session_id] = current
                changed = True
        return state if changed else None

    update_state(session_id, _clear)
    if recorded is None:
        return None
    before = recorded.get("snapshot")
    if not isinstance(cwd, str) or not isinstance(before, dict):
        return None
    after = _git_status.snapshot_remote_refs(cwd)
    changed_remotes = sorted(_diff_remote_snapshots(before, after))
    if not changed_remotes:
        return None
    return _llm_notice(
        "warn: remote refs changed during a codex call "
        f"(remotes: {', '.join(changed_remotes)})."
        " This may reflect an unintended `git push`/tag creation performed inside the"
        " codex process (which bypasses PreToolUse), or a legitimate push by another"
        " concurrent session that cannot be distinguished from here. Verify the remote"
        " state and, if the change was unintended, restore it per caller-reception.md"
        " remote-state reconciliation.",
        tag="warn",
    )


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
    if event_name == "PermissionDenied":
        return None
    if event_name == "PostToolUseFailure" and tool_name not in _CODEX_APP_SERVER_START_TOOLS:
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


def _record_plan_mode_entered(session_id: str) -> None:
    """新しい計画区切りで振り返り済み状態を解除する。"""

    def _reset_review_invoked(state: dict) -> dict | None:
        if not state.get("session_review_invoked"):
            return None
        state["session_review_invoked"] = {}
        return state

    update_state(session_id, _reset_review_invoked)


def _record_skill_use(session_id: str, skill_name: object, *, is_sidechain: bool) -> None:
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
    if skill_name in _SESSION_REVIEW_SKILL_NAMES:

        def _set_review_invoked(state: dict) -> dict | None:
            invoked = state.get("session_review_invoked")
            if not isinstance(invoked, dict):
                invoked = {}
            if invoked.get(skill_name) is True:
                return None
            invoked[skill_name] = True
            state["session_review_invoked"] = invoked
            return state

        update_state(session_id, _set_review_invoked)
    if skill_name in _PROCESS_FEEDBACKS_SKILL_NAMES:
        update_state(session_id, _set_process_feedbacks_invoked)
    if skill_name in _PLAN_AND_ADD_FEEDBACK_SKILL_NAMES:

        def _set_plan_and_add_feedback_invoked(state: dict) -> dict | None:
            if state.get("plan_and_add_feedback_skill_invoked", False):
                return None
            state["plan_and_add_feedback_skill_invoked"] = True
            return state

        update_state(session_id, _set_plan_and_add_feedback_invoked)
    if skill_name in _ADD_FEEDBACK_SKILL_NAMES:

        def _set_add_feedback_invoked(state: dict) -> dict | None:
            if state.get("add_feedback_skill_invoked", False):
                return None
            state["add_feedback_skill_invoked"] = True
            return state

        update_state(session_id, _set_add_feedback_invoked)
    if skill_name in _EXIT_SESSION_SKILL_NAMES:
        update_state(session_id, _reset_process_feedbacks_invoked)
    if not is_sidechain and skill_name in _DELEGATION_SKILL_NAMES:

        def _set_delegation_invoked(state: dict) -> dict | None:
            if state.get("delegation_skill_invoked", False):
                return None
            state["delegation_skill_invoked"] = True
            return state

        update_state(session_id, _set_delegation_invoked)


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
        if is_plan_file(display_path):
            _record_plan_file(session_id, display_path)
        if is_agent_facing_md(display_path):
            _append_conditional_prohibition_notice(operation.path, display_path, notices)
        if plan_mode_invoked and is_plan_file(display_path) and operation.is_whole_write:
            notices.append(_plan_file_check_notice(display_path, cwd))


def _append_conditional_prohibition_notice(read_path: str, display_path: str, notices: list[str]) -> None:
    """適用後の実ファイルを読み、条件付き禁止形の警告があれば通知へ加える。"""
    try:
        content = pathlib.Path(read_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return
    warnings = _check_conditional_prohibition(pathlib.Path(display_path), content)
    if warnings:
        notices.append(_llm_notice("\n".join(warnings), tag="warn"))


def _plan_file_check_notice(file_path: str, cwd: str) -> str:
    """計画ファイル全文書き込み後に実行する機械検査の案内文を返す。"""
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
        tbd_notice = _tbd_completion.build_notice(session_id, cwd, payload.get("transcript_path", ""))
        if tbd_notice is not None:
            notices.append(_llm_notice(tbd_notice, tag="notice"))

    # pyfltr MCPのrun_for_agentはPostToolUseへ到達した時点で成功済みである。
    # CLI経由と同じ検証完了契約として記録し、コミット前の未検証警告を抑制する。
    if tool_name == _PYFLTR_RUN_FOR_AGENT_TOOL_NAME:
        _record_test_executed(session_id)
        return 0

    # EnterPlanMode: 新規作業区切りとしてsession_review_invokedをリセット
    if tool_name == "EnterPlanMode":
        _record_plan_mode_entered(session_id)
        return 0

    # Skill: plan-modeスキル呼び出し検出と振り返りスキル呼び出し検出
    if tool_name == "Skill":
        _record_skill_use(session_id, tool_input.get("skill"), is_sidechain=payload.get("isSidechain") is True)
        return 0

    # AgentとTask: subagent_type別セッション状態フラグ記録 + process-loop観測用の終了時刻記録 (fb-1)
    if tool_name in ("Agent", "Task"):
        subagent_type = tool_input.get("subagent_type")
        if isinstance(subagent_type, str) and subagent_type in _TRACKED_SUBAGENT_TYPES:
            _process_loop_log.append("subagent_end", type=subagent_type)
        return 0

    # 開始点のstructuredContentからsession_id→cwdを保存し、codex_result受領時だけ
    # 開始時点のremote refと比較する。status/waitは稼働中のため比較を完了しない。
    if tool_name in _CODEX_APP_SERVER_TOOL_NAMES:
        if payload.get("hook_event_name") == "PostToolUseFailure":
            if tool_name == _CODEX_APP_SERVER_REPLY_TOOL:
                _record_codex_reply_failure(payload, session_id)
            elif tool_name == _CODEX_APP_SERVER_RESULT_TOOL and _codex_result_failure_keeps_snapshot(session_id, payload):
                return 0
            else:
                _clear_codex_remote_snapshot(payload, session_id)
            return 0
        tool_response = payload.get("tool_response", {})
        structured = tool_response.get("structuredContent") if isinstance(tool_response, dict) else None
        if not isinstance(structured, dict):
            structured = tool_response if isinstance(tool_response, dict) else {}
        if tool_name == _CODEX_APP_SERVER_RESULT_TOOL:
            _record_codex_session_state(session_id, structured)

            def _mark_result_retrieved(state: dict) -> dict | None:
                remote_session_id = structured.get("session_id")
                sessions = state.get(_CODEX_SESSION_STATE_KEY)
                if not isinstance(remote_session_id, str) or not isinstance(sessions, dict):
                    return None
                record = sessions.get(remote_session_id)
                if not isinstance(record, dict) or record.get("result_retrieved") is True:
                    return None
                record["result_retrieved"] = True
                sessions[remote_session_id] = record
                return state

            update_state(session_id, _mark_result_retrieved)
            codex_notice = _warn_codex_remote_change(session_id, payload)
            if codex_notice is not None:
                notices.append(codex_notice)
        elif tool_name in (_CODEX_APP_SERVER_START_TOOL, _CODEX_APP_SERVER_REPLY_TOOL):
            _record_codex_session_cwd(session_id, payload)
        else:
            _record_codex_session_state(session_id, structured)
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
