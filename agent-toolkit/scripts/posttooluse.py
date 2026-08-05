r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録とplan file形式検査。

Bash / Write / Edit / MultiEdit / Skill / Read / EnterPlanMode / Agent / Taskの実行後にイベントを検出し、
セッション状態ファイルに記録する。
PreToolUseやStopフックが参照して警告・提案の判定に使う。

検出対象:

1. テスト実行 (Bash / pyfltr MCPの`run_for_agent`)
2. git log確認状態の記録・リセット (Bash: logで記録、対象コミットの親子関係が
   変化する操作＝commit/rebase/resetでリセット)
3. plan file（`~/.claude/plans/*.md`）形式検査 (Write / Edit / MultiEdit)
4. plan-modeスキル呼び出し検出 (Skill)
5. 振り返りスキル呼び出し検出 (Skill)
   （`session_review_invoked`辞書へ記録）
6. textlint-violations.md読み込み検出 (Read)
7. 新規作業区切りでの`session_review_invoked`リセット (EnterPlanMode)
8. `_TRACKED_SUBAGENT_TYPES`対象種別のサブエージェント終了時刻の`_process_loop_log`記録
9. codex MCP呼び出し後のリモートref変化確認
10. exit-session起動検知による`process_feedbacks_skill_invoked`フラグのリセット (Skill)。
    `plan-and-add-feedback`起動検知による`plan_and_add_entries_skill_invoked`フラグの設定と、
    `process-feedbacks`起動検知による同フラグのリセットも同経路で扱う (Skill)
11. 現在の計画ファイルパス記録 (Write / Edit / MultiEdit、plan file判定時)
    （pretooluse.py側の遡及スキャン記録検査が計画ファイル本文を再読み込みする際に使用）
12. 編集ファイルパス蓄積（Write / Edit / MultiEdit、`session_edited_files`リストへ追記）
    （pretooluse.py側の一括ステージ警告で自セッション編集対象の判定に使用）
13. `git commit --amend` / `git commit --fixup` 成功時のcwd別
    `amend_pending_status_check`フラグ設定（pretooluse.py側の`git push`前dirty検査で参照）
14. `git push`（`--dry-run` / `-n`以外）成功時の該当cwd`amend_pending_status_check`フラグ解除
15. PostToolUseFailure・PermissionDenied: 状態を変更せず終了
16. 条件付き禁止形（「〜した状態で…しない/禁止」）の警告検出 (Write / Edit / MultiEdit、
    `is_agent_facing_md`が対象と判定するコーディングエージェント向け`.md`編集時)
17. `agent-toolkit:codex-exec`起動の記録 (Skill)
18. 対象リポジトリのTBDが全件回答済みへ遷移した場合の通知（全ツール共通）
"""

import hashlib
import json
import pathlib
import re
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills" / "plan-mode" / "scripts"))
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _process_loop_log  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _tbd_completion  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import extract_git_events  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_format import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_h2_sections,
    extract_h3_headings_under_h2,
    is_agent_facing_md,
)
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


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


# --- Bashコマンド前処理 ---

# コマンド先頭またはセグメント区切り（`;`・`&`・`|`）直後の`KEY=VALUE`代入を捕捉する。
# `_ENV_ASSIGN_PREFIX_PATTERN.sub`で代入連続を除去し、先頭の区切り文字＋空白は維持する。
_ENV_ASSIGN_PREFIX_PATTERN = re.compile(r"(\A|[;&|])(\s*)(?:[A-Za-z_]\w*=\S*\s+)+")


def _strip_env_assignments(command: str) -> str:
    """コマンド先頭・セグメント区切り直後の環境変数代入接頭辞（`KEY=VALUE`）を除去する。

    用途: テスト実行検出やgit操作検出の正規表現が、`LOCALAPPDATA=/tmp/dummy uvx pyfltr ...`
    のような環境変数代入接頭辞付きコマンドにマッチしない問題に追従する。
    適用範囲: Bashコマンド文字列。`KEY=VALUE`の単純形式のみを対象とし、
    クォート内に空白を含む値・`env`コマンド経由・行継続バックスラッシュ等の特殊形式は対象外とする。
    """
    return _ENV_ASSIGN_PREFIX_PATTERN.sub(r"\1\2", command)


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

# exit-sessionスキル呼び出し検出。process-feedbacksのフラグリセット経路に使う
# （process-feedbacks/SKILL.md「ステップ8: 振り返りとセッション終了」がexit-sessionで終端する）。
_EXIT_SESSION_SKILL_NAMES = frozenset({"agent-toolkit:exit-session", "exit-session"})

_PLAN_AND_ADD_FEEDBACK_SKILL_NAMES = frozenset({"agent-toolkit:plan-and-add-feedback", "plan-and-add-feedback"})
_CODEX_EXEC_SKILL_NAMES = frozenset({"agent-toolkit:codex-exec", "codex-exec"})

# codex呼び出し前後のリモート参照スナップショットを記録する状態辞書のキー。
# `pretooluse.py`が同一キーで書き込み、本スクリプトが読み取り・削除する共有SSOT。
_CODEX_REMOTE_SNAPSHOT_KEY = "codex_remote_snapshot_by_key"


def _codex_thread_cwd_state_id(thread_id: str) -> str:
    """`threadId`単位の共有cwd状態ファイルに使う疑似セッションIDを返す。"""
    digest = hashlib.sha256(thread_id.encode()).hexdigest()
    return f"codex-thread-cwd-{digest}"


# 条件付き禁止形（「〜した状態で…しない/禁止」）検出パターン。
# 「Xした状態でYしない」形式は「Xでなければ`Y`してよい」と誤読され得るため、
# 全称否定形（「いかなる理由があっても`Y`しない」）または肯定的完遂義務への
# 書き換えを促す。初期段階の限定的なパターンであり、将来の検出範囲拡張は拡張候補とする。
# 全角鍵括弧・バッククォート囲みの引用文脈（他ファイル節名・識別子・規範文言の引用）は
# 照合前に無害化する。`_scope_escalation._apply_category_exclusions`は該当区間を空文字へ完全除去するが、
# 本実装は行番号算出（`content`上のオフセットをそのまま使う）を成立させるため文字数を保ったまま
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
    """process-feedbacksスキル起動フラグを偽へ戻す。既に偽ならNoneを返す（冪等）。"""
    if not state.get("process_feedbacks_skill_invoked", False):
        return None
    state["process_feedbacks_skill_invoked"] = False
    return state


def _set_plan_and_add_entries_invoked(state: dict) -> dict | None:
    """plan-and-add-feedbackスキル起動フラグを常時Trueへ上書きする。"""
    state["plan_and_add_entries_skill_invoked"] = True
    return state


def _reset_plan_and_add_entries_invoked(state: dict) -> dict | None:
    """process-feedbacks起動検知（plan-and-add-feedbackの終端工程が委譲する先）でフラグをリセットする。

    `plan-and-add-feedback/SKILL.md`「手順」節2は`agent-toolkit:process-feedbacks`
    「フィードバック投入」節を参照呼び出しして終端するため、当該スキルの起動を終端シグナルとする。
    """
    if not state.get("plan_and_add_entries_skill_invoked", False):
        return None
    state["plan_and_add_entries_skill_invoked"] = False
    return state


def _check_plan_format(file_path: str) -> list[str]:
    """Plan fileの構成を検査して違反メッセージの一覧を返す。

    検出する違反は`## 変更内容`配下の先頭H3が「対象ファイル一覧」でないこと。
    `agent-toolkit/skills/plan-mode/SKILL.md`「計画ファイルの完成条件」節の
    `## 変更内容`の項（冒頭に`### 対象ファイル一覧`を置く規定）に対応する。

    読み取り失敗時は空リストを返す。
    H2節順違反（必須H2欠落・順序違反・予期せぬH2）はPreToolUseのWriteブロックへ移管済み。
    """
    try:
        content = pathlib.Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    headings = extract_h2_sections(content)
    violations: list[str] = []

    # 変更内容H2 配下の先頭H3が「対象ファイル一覧」かを検査する
    if "変更内容" in headings:
        h3_list = extract_h3_headings_under_h2(content, "変更内容")
        first_h3 = h3_list[0] if h3_list else None
        if first_h3 != "対象ファイル一覧":
            actual = first_h3 if first_h3 is not None else "(no H3 present)"
            violations.append(f"the first H3 under '## 変更内容' must be '対象ファイル一覧', but found: '{actual}'.")

    return violations


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


def _warn_codex_remote_change(session_id: str, payload: dict) -> str | None:
    """codex呼び出し前後でリモート参照が変化した場合に警告本文を返す。

    PreToolUse側が記録をスキップした場合（`cwd`未取得等）は比較せず終了する。
    比較後は記録済みスナップショットを削除し、次回呼び出しでの記録漏れによる
    古いスナップショットとの誤比較を防ぐ。警告はコーディングエージェントへ確実に届ける
    ため`hookSpecificOutput.additionalContext`経由で出力する
    （`agent-toolkit/skills/agent-standards/references/claude-hooks.md`
    「出力フィールドの使い分け」節: PostToolUseで行動を促す場合の第一経路）。
    """
    agent_id = _extract_transcript_agent_id(payload.get("transcript_path"))
    key = agent_id if agent_id is not None else f"session:{session_id}"
    state = read_state(session_id)
    entries = state.get(_CODEX_REMOTE_SNAPSHOT_KEY)
    recorded = entries.get(key) if isinstance(entries, dict) else None

    def _clear(state: dict) -> dict | None:
        entries = state.get(_CODEX_REMOTE_SNAPSHOT_KEY)
        if isinstance(entries, dict) and key in entries:
            del entries[key]
            return state
        return None

    tool_response = payload.get("tool_response", {})
    thread_id = tool_response.get("threadId") or tool_response.get("thread_id") if isinstance(tool_response, dict) else None
    cwd = recorded.get("cwd") if isinstance(recorded, dict) else None
    if isinstance(thread_id, str) and thread_id and isinstance(cwd, str) and cwd:

        def _record_thread_cwd(state: dict) -> dict | None:
            if state.get("cwd") == cwd:
                return None
            state["cwd"] = cwd
            return state

        update_state(_codex_thread_cwd_state_id(thread_id), _record_thread_cwd)

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


def _dispatch(payload_text: str, notices: list[str]) -> int:
    """payloadを解析し、通知本文を`notices`へ蓄積する。終了コードは常に0。"""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    hook_event_name = payload.get("hook_event_name", "")
    if hook_event_name in ("PostToolUseFailure", "PermissionDenied"):
        return 0

    # cwdはBash分岐とTBD回答完了の検査で使うため、分岐前に一度だけ取得する。
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""

    # 対象リポジトリのTBDが全件回答済みへ遷移した場合に通知する。
    # ツール種別に依らず検査し、ユーザーの回答から通知までの遅延を抑える。
    if cwd:
        tbd_notice = _tbd_completion.build_notice(session_id, cwd, payload.get("transcript_path", ""))
        if tbd_notice is not None:
            notices.append(_llm_notice(tbd_notice, tag="notice"))

    # pyfltr MCPのrun_for_agentはPostToolUseへ到達した時点で成功済みである。
    # CLI経由と同じ検証完了契約として記録し、コミット前の未検証警告を抑制する。
    if tool_name == _PYFLTR_RUN_FOR_AGENT_TOOL_NAME:

        def _set_test_executed(state: dict) -> dict | None:
            if state.get("test_executed", False):
                return None
            state["test_executed"] = True
            return state

        update_state(session_id, _set_test_executed)
        return 0

    # EnterPlanMode: 新規作業区切りとしてsession_review_invokedをリセット
    if tool_name == "EnterPlanMode":

        def _reset_review_invoked(state: dict) -> dict | None:
            if not state.get("session_review_invoked"):
                return None
            state["session_review_invoked"] = {}
            return state

        update_state(session_id, _reset_review_invoked)
        return 0

    # Skill: plan-modeスキル呼び出し検出と振り返りスキル呼び出し検出
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:

            def _set_invoked(state: dict) -> dict | None:
                if state.get("plan_mode_skill_invoked", False):
                    return None
                state["plan_mode_skill_invoked"] = True
                return state

            update_state(session_id, _set_invoked)
        if isinstance(skill_name, str) and skill_name in _SESSION_REVIEW_SKILL_NAMES:

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
        if isinstance(skill_name, str) and skill_name in _PROCESS_FEEDBACKS_SKILL_NAMES:
            update_state(session_id, _set_process_feedbacks_invoked)
            update_state(session_id, _reset_plan_and_add_entries_invoked)
        if isinstance(skill_name, str) and skill_name in _EXIT_SESSION_SKILL_NAMES:
            update_state(session_id, _reset_process_feedbacks_invoked)
        if isinstance(skill_name, str) and skill_name in _PLAN_AND_ADD_FEEDBACK_SKILL_NAMES:
            update_state(session_id, _set_plan_and_add_entries_invoked)
        if isinstance(skill_name, str) and skill_name in _CODEX_EXEC_SKILL_NAMES:

            def _set_codex_exec_invoked(state: dict) -> dict | None:
                if state.get("codex_exec_skill_invoked", False):
                    return None
                state["codex_exec_skill_invoked"] = True
                return state

            update_state(session_id, _set_codex_exec_invoked)
        return 0

    # AgentとTask: subagent_type別セッション状態フラグ記録 + process-loop観測用の終了時刻記録 (fb-1)
    if tool_name in ("Agent", "Task"):
        subagent_type = tool_input.get("subagent_type")
        if isinstance(subagent_type, str) and subagent_type in _TRACKED_SUBAGENT_TYPES:
            _process_loop_log.append("subagent_end", type=subagent_type)
        return 0

    # codex呼び出し後はリモートrefの変化だけを確認する。
    if tool_name in ("mcp__codex__codex", "mcp__codex__codex-reply"):
        codex_notice = _warn_codex_remote_change(session_id, payload)
        if codex_notice is not None:
            notices.append(codex_notice)
        return 0

    # Read: 規範ファイル読み込みのセッション状態フラグ化
    if tool_name == "Read":
        file_path_raw = tool_input.get("file_path")
        if isinstance(file_path_raw, str):
            # Windowsからのバックスラッシュ区切りを正規化してから判定する
            file_path_normalized = file_path_raw.replace("\\", "/")
            if file_path_normalized.endswith("writing-standards/references/textlint-violations.md"):

                def _set_textlint_violations_read(state: dict) -> dict | None:
                    if state.get("textlint_violations_read", False):
                        return None
                    state["textlint_violations_read"] = True
                    return state

                update_state(session_id, _set_textlint_violations_read)
        return 0

    # Write / Edit / MultiEdit: ファイル編集は対象コミットの親子関係を変えないため
    # git_log_checkedをリセットしない（リセット対象は`_GIT_LOG_RESET_SUBCOMMANDS`が定める
    # commit / rebase / resetのみとする）。
    if tool_name in ("Write", "Edit", "MultiEdit"):
        # plan file形式検査: ~/.claude/plans/直下の.mdのみ対象。
        # plan-modeスキル未呼び出し時はPreToolUse側の警告で先行催促済みのため、
        # 構造検査をスキップして二重警告を避ける。
        state = read_state(session_id)
        file_path_raw = tool_input.get("file_path")
        file_path = file_path_raw if isinstance(file_path_raw, str) else ""
        if is_plan_file(file_path):
            # 現在の計画ファイルパスを記録する。
            # pretooluse.py側の遡及スキャン記録検査が再読み込みする用途に使う。

            def _set_current_plan_file_path(current_state: dict, file_path: str = file_path) -> dict | None:
                if current_state.get("current_plan_file_path") == file_path:
                    return None
                current_state["current_plan_file_path"] = file_path
                return current_state

            update_state(session_id, _set_current_plan_file_path)
        # 自セッション編集済みファイルパス蓄積。
        # pretooluse.pyの一括ステージ警告（_check_bash_bulk_stage_with_unedited_files）が
        # 「自セッション編集済み集合」として参照する。パスは取得したままの形式で蓄積し、
        # 参照側で正規化する。
        if file_path:

            def _append_edited_file(current_state: dict, target: str = file_path) -> dict | None:
                edited = current_state.get("session_edited_files", [])
                if not isinstance(edited, list):
                    return None
                if target in edited:
                    return None
                edited.append(target)
                current_state["session_edited_files"] = edited
                return current_state

            update_state(session_id, _append_edited_file)
        # 条件付き禁止形の警告通知: `is_agent_facing_md`が対象と判定するコーディングエージェント向け
        # `.md`編集時に、plan-mode起動状態と無関係に常時検査する（対象判定は既存SSOTを再利用）。
        if is_agent_facing_md(file_path):
            try:
                prohibition_content = pathlib.Path(file_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                prohibition_content = None
            if prohibition_content is not None:
                prohibition_warnings = _check_conditional_prohibition(pathlib.Path(file_path), prohibition_content)
                if prohibition_warnings:
                    notices.append(_llm_notice("\n".join(prohibition_warnings), tag="warn"))
        # 計画ファイル向け通知: 形式検査違反（plan-mode起動時のみ）と、
        # Write成功時の書き込み後チェック案内（plan-mode起動時のみ）を1つのadditionalContextへまとめる。
        # 状態フラグは追加せず、案内のみを一方向で通知する。
        if state.get("plan_mode_skill_invoked", False) and is_plan_file(file_path):
            messages: list[str] = []
            violations = _check_plan_format(file_path)
            if violations:
                messages.append(
                    _llm_notice(
                        f"plan file {file_path} does not conform to the expected structure."
                        f" {' '.join(violations)}"
                        f" Fix the structure per the '計画ファイルの完成条件' section of"
                        f" agent-toolkit/skills/plan-mode/SKILL.md (read it first if not yet).",
                        tag="warn",
                    )
                )
            if tool_name == "Write":
                # 案内文はそのまま実行される。プラグインルートを本プロセスの位置から絶対パスで解決し、
                # `--work-dir`へpayloadのcwdを埋める。リポジトリ相対のパスは配布元以外で解決できず、
                # `${CLAUDE_PLUGIN_ROOT}`はコマンドを実行するシェルの環境に存在しない。
                check_script = pathlib.Path(__file__).resolve().parents[1] / "skills/plan-mode/scripts/check_plan_file.py"
                work_dir_option = f" --work-dir {shlex.quote(cwd)}" if cwd else ""
                messages.append(
                    _llm_notice(
                        f"plan file {file_path} was written. Run the post-write checks:"
                        f" `uv run --script {shlex.quote(str(check_script))}{work_dir_option}"
                        f" {shlex.quote(file_path)}`."
                        " Replace --work-dir if the plan targets a repository other than the session's"
                        " working directory.",
                        tag="notice",
                    )
                )
            notices.extend(messages)
        return 0

    # Bash以外はここで終了
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0

    # 環境変数代入接頭辞（`LOCALAPPDATA=...`等）を除去してから検出パターンを適用する。
    command = _strip_env_assignments(command)

    git_events = extract_git_events(command, cwd)

    def _apply_bash_updates(state: dict) -> dict | None:
        changed = False
        # テスト実行の検出
        if not state.get("test_executed", False):
            for pattern in _TEST_PATTERNS:
                if pattern.search(command):
                    state["test_executed"] = True
                    changed = True
                    break

        # git_log_checked: log で記録、commit / rebase / reset でリセット。
        # cwd別の辞書`{cwd: True}`で記録する。cwd空イベントは旧形式の単一bool値で記録する。
        log_state = state.get("git_log_checked")
        log_modified = False
        for event in git_events:
            if event.subcommand == "log":
                if event.cwd:
                    if not isinstance(log_state, dict):
                        log_state = {}
                    if not log_state.get(event.cwd, False):
                        log_state[event.cwd] = True
                        log_modified = True
                elif not isinstance(log_state, dict) and not log_state:
                    log_state = True
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

        # git commit --amend / --fixup 成功時にcwd別のamend後dirty検査フラグを立てる。
        # 実送出`git push`（`--dry-run`/`-n`以外）成功時に該当cwdフラグを解除する
        # （dry-run時はpretooluse側でも解除しないため、posttooluse側でも解除しない）。
        for event in git_events:
            if event.subcommand == "commit" and _git_commit_is_amend_or_fixup(event.subcommand_args):
                if _set_amend_pending_status_check(state, event.cwd) is not None:
                    changed = True
            elif (
                event.subcommand == "push"
                and _git_status.git_push_is_real_send(event.subcommand_args)
                and _reset_amend_pending_status_check(state, event.cwd) is not None
            ):
                changed = True

        return state if changed else None

    update_state(session_id, _apply_bash_updates)
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
