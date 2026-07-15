#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["platformdirs>=4.0"]
# ///
r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録とplan file形式検査。

Bash / Write / Edit / MultiEdit / Skill / Read / EnterPlanMode / Agent / Taskの実行後にイベントを検出し、
セッション状態ファイルに記録する。
PreToolUseやStopフックが参照して警告・提案の判定に使う。

検出対象:

1. テスト実行 (Bash)
2. Git状態確認 (Bash) とgit log確認状態のリセット (commit/rebase/push/編集後)
3. plan file（`~/.claude/plans/*.md`）形式検査 (Write / Edit / MultiEdit)
4. plan-modeスキル呼び出し検出 (Skill)
5. 振り返りスキル呼び出し検出 (Skill)
   （`session_review_invoked`辞書へ記録）
6. codex-review.md読み込み検出 (Read)
7. 新規作業区切りでの`session_review_invoked`リセット (EnterPlanMode)
8. AgentとTask両呼び出し時のsubagent_type別セッション状態フラグ記録
   （plan-reviewer / plan-impl-reviewer / agent-doc-validator / plan-codex-reviewer）
   および`_TRACKED_SUBAGENT_TYPES`対象種別のサブエージェント終了時刻の`_process_loop_log`記録
9. codex-review起動検出（Agent/Task: subagent_typeがplan-codex-reviewer / mcp__codex__codexツール）
10. codex-impl起動検出（Skill: agent-toolkit:codex-impl。`codex_impl_invoked`記録）
11. process-feedbacks-finish起動検知による`process_feedbacks_skill_invoked`フラグのリセット (Skill)
12. 現在の計画ファイルパス記録 (Write / Edit / MultiEdit、plan file判定時)
    （pretooluse.py側の`agent_doc_validator_invoked`条件付き必須化判定に使用）
13. 編集ファイルパス蓄積（Write / Edit / MultiEdit、`session_edited_files`リストへ追記）
    （pretooluse.py側の一括ステージ警告で自セッション編集対象の判定に使用）
14. `git commit --amend` / `git commit --fixup` 成功時のcwd別
    `amend_pending_status_check`フラグ設定（pretooluse.py側の`git push`前dirty検査で参照）
15. `git push`（`--dry-run` / `-n`以外）成功時の該当cwd`amend_pending_status_check`フラグ解除
"""

import json
import pathlib
import re
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "skills" / "plan-mode" / "scripts"))
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _process_loop_log  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import extract_git_events  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_diff_parsing import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_section_with_offset,
    iter_reduction_headings,
)
from _plan_file import is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_format import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_h2_section_body,
    extract_h2_sections,
    extract_h3_headings_under_h2,
    extract_target_files_from_changes,
    is_agent_facing_md,
)
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from check_wc_projection import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_addition_reduction_blocks,
)

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/posttooluse"


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
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+|uvx\s+)?pre-commit\s+run\b"),
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

# `git status` / `git log` / `git diff` のいずれかを実行した場合に状態確認済みとみなす。
_GIT_STATUS_SUBCOMMANDS: frozenset[str] = frozenset({"status", "log", "diff"})

# git_log_checked をリセットするサブコマンド（既存コミットを書き換える・送出する系統）。
_GIT_LOG_RESET_SUBCOMMANDS: frozenset[str] = frozenset({"commit", "rebase", "push"})


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

# codex-impl起動検出に使うスキル名。フルネームと短縮名の両方を許容する。
# pretooluse.pyの`_PLAN_IMPL_EXECUTOR_SUBAGENT_TYPES`と同型の構成とする。
_CODEX_IMPL_SKILL_NAMES = frozenset({"agent-toolkit:codex-impl", "codex-impl"})

# process-feedbacksスキル呼び出し検出。フルネームとスラッシュコマンド短縮名の両方を許容する。
# Stop hookの拡張照合カテゴリ有効化判定に使う。
_PROCESS_FEEDBACKS_SKILL_NAMES = frozenset({"agent-toolkit:process-feedbacks", "process-feedbacks"})

# process-feedbacks-finishスキル呼び出し検出。フラグリセット経路の第1経路として使う。
_PROCESS_FEEDBACKS_FINISH_SKILL_NAMES = frozenset({"agent-toolkit:process-feedbacks-finish", "process-feedbacks-finish"})

# AgentツールとTaskツールのsubagent_type別セッション状態フラグ記録。
# フルネームと短縮名の両方を許容する。
_SUBAGENT_TYPE_FLAGS: dict[str, str] = {
    "plan-reviewer": "plan_reviewer_invoked",
    "agent-toolkit:plan-reviewer": "plan_reviewer_invoked",
    "plan-impl-reviewer": "plan_impl_reviewer_invoked",
    "agent-toolkit:plan-impl-reviewer": "plan_impl_reviewer_invoked",
    "agent-doc-validator": "agent_doc_validator_invoked",
    "agent-toolkit:agent-doc-validator": "agent_doc_validator_invoked",
    "plan-codex-reviewer": "codex_review_invoked",
    "agent-toolkit:plan-codex-reviewer": "codex_review_invoked",
}

# `_process_loop_log`による終了時刻記録の対象サブエージェント種別（fb-1）。
# `pretooluse.py`側の同名定数（起動時刻記録用）と対応させる。
# フルネームと短縮名の両方を許容する。
_TRACKED_SUBAGENT_TYPES: frozenset[str] = frozenset(
    {
        "plan-impl-executor",
        "agent-toolkit:plan-impl-executor",
        "plan-implementer",
        "agent-toolkit:plan-implementer",
        "plan-impl-reviewer",
        "agent-toolkit:plan-impl-reviewer",
        "plan-codex-reviewer",
        "agent-toolkit:plan-codex-reviewer",
        "plan-reviewer",
        "agent-toolkit:plan-reviewer",
        "plan-spec-reviewer",
        "agent-toolkit:plan-spec-reviewer",
        "agent-doc-validator",
        "agent-toolkit:agent-doc-validator",
    }
)

# --- plan file形式検査の定数 ---


def _set_process_feedbacks_invoked(state: dict) -> dict | None:
    """process-feedbacksスキル起動フラグを常時Trueへ上書きする。

    新規process-feedbacksラン開始時に前ランの残置フラグを無視して確実にTrueへ強制上書きするため冪等スキップを廃止する。
    リセット経路は`_reset_process_feedbacks_invoked`（process-feedbacks-finish完了検知）と併用する。
    """
    state["process_feedbacks_skill_invoked"] = True
    return state


def _reset_process_feedbacks_invoked(state: dict) -> dict | None:
    """process-feedbacksスキル起動フラグを偽へ戻す。既に偽ならNoneを返す（冪等）。"""
    if not state.get("process_feedbacks_skill_invoked", False):
        return None
    state["process_feedbacks_skill_invoked"] = False
    return state


def _collect_reduction_heading_files(content: str) -> set[str]:
    """`## 変更内容`配下の`#### 縮減対象（<ファイル名>）`H4見出しからファイル名集合を抽出する。

    完全パスとbasenameのどちらの記述も許容する運用のため、記載通りの文字列をそのまま集合へ格納する
    （呼び出し側でパス・basenameのいずれとの一致でも除外対象と判定する）。
    フェンス内・インラインコード等の除外領域は`extract_h2_section_body`側で処理済みとする。
    正規表現SSOTは`_plan_diff_parsing.iter_reduction_headings`とし、当モジュールは共通ヘルパーを再利用する。
    """
    section_text = "\n".join(line for _lineno, line in extract_h2_section_body(content, "変更内容"))
    return set(iter_reduction_headings(section_text))


def _check_target_file_line_counts(content: str, cwd: str) -> str | None:
    """対象ファイル一覧の各パスの行数を確認し、220行超過の対象種別ファイルがあれば警告メッセージを返す。

    `## 変更内容`配下に対応する`#### 縮減対象（<ファイル名>）`H4見出しが存在するファイル、
    および`[現行]`/`[置換後]`ペア・`[削除根拠]`ペアで縮減量・置換ペアが計上済みのファイルは、
    縮減計画済みとして警告対象から除外する（`check_wc_projection.py`の判定条件と同一のSSOTを使う）。
    ファイル名は完全パス表記・basename表記のいずれも許容する。
    """
    paths = extract_target_files_from_changes(content)
    if not paths:
        return None
    reduction_files = _collect_reduction_heading_files(content)
    section_text, _offset = extract_section_with_offset(content, "## 変更内容")
    addition_reduction = extract_addition_reduction_blocks(section_text or "")
    base = pathlib.Path(cwd) if cwd else pathlib.Path.cwd()
    over_limit: list[tuple[str, int]] = []
    for rel in paths:
        if not is_agent_facing_md(rel):
            continue
        # 完全パス一致・basename一致のいずれかで縮減対象H4見出しが存在する場合は除外する。
        basename = rel.rsplit("/", 1)[-1]
        if rel in reduction_files or basename in reduction_files:
            continue
        entry = addition_reduction.get(rel, {})
        if entry.get("replacement_pair_count", 0) > 0 or entry.get("reduction", 0) > 0:
            continue
        target = base / rel
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        if line_count > 220:
            over_limit.append((rel, line_count))
    if not over_limit:
        return None
    listed = ", ".join(f"{p} ({n} lines)" for p, n in over_limit)
    return (
        f"plan file contains target files exceeding 220 lines: {listed}."
        " Per agent-standards 'document size limit' section (over 220 lines is a violation),"
        " add a `#### 縮減対象（<ファイル名>）` H4 section under `## 変更内容` for each violation."
        " Write the file name in full path form."
    )


def _mark_line_count_warned(session_id: str, file_path: str) -> None:
    """対象ファイル行数超過警告を発火済みとしてセッション状態へアトミックに記録する。"""

    def _mutator(state: dict, file_path: str = file_path) -> dict | None:
        warned = state.get("plan_target_file_line_count_warned", {})
        if not isinstance(warned, dict):
            warned = {}
        if warned.get(file_path, False):
            return None
        warned[file_path] = True
        state["plan_target_file_line_count_warned"] = warned
        return state

    update_state(session_id, _mutator)


def _line_count_already_warned(session_id: str, file_path: str) -> bool:
    """対象ファイル行数超過警告が当該計画ファイルへ既に発火済みかを判定する。"""
    warned = read_state(session_id).get("plan_target_file_line_count_warned", {})
    return isinstance(warned, dict) and warned.get(file_path, False) is True


def _check_plan_format(file_path: str, cwd: str, session_id: str) -> list[str]:
    """Plan fileの構成を検査して違反メッセージの一覧を返す。

    検出する違反:

    - `## 変更内容`配下の先頭H3が「対象ファイル一覧」でない
    - `## 変更内容 > ### 対象ファイル一覧`配下の対象種別ファイルが220行以上
      （同一計画ファイルへ1度発火済みの場合は抑止し、H3順序違反等の他違反は毎回発火継続する）

    読み取り失敗時は空リストを返す。
    H2節順違反（必須H2欠落・順序違反・予期せぬH2）はPreToolUseのWriteブロックへ移管済み。
    絶対行番号の直書き検査もPreToolUseへ移管済み。
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

    line_count_warning = _check_target_file_line_counts(content, cwd)
    if line_count_warning and not _line_count_already_warned(session_id, file_path):
        violations.append(line_count_warning)
        _mark_line_count_warned(session_id, file_path)

    return violations


def main() -> int:
    """エントリポイント。終了コードは常に0。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
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
        if isinstance(skill_name, str) and skill_name in _CODEX_IMPL_SKILL_NAMES:

            def _set_codex_impl_invoked(state: dict) -> dict | None:
                if state.get("codex_impl_invoked", False):
                    return None
                state["codex_impl_invoked"] = True
                return state

            update_state(session_id, _set_codex_impl_invoked)
        if isinstance(skill_name, str) and skill_name in _PROCESS_FEEDBACKS_SKILL_NAMES:
            update_state(session_id, _set_process_feedbacks_invoked)
        if isinstance(skill_name, str) and skill_name in _PROCESS_FEEDBACKS_FINISH_SKILL_NAMES:
            update_state(session_id, _reset_process_feedbacks_invoked)
        return 0

    # AgentとTask: subagent_type別セッション状態フラグ記録 + process-loop観測用の終了時刻記録 (fb-1)
    if tool_name in ("Agent", "Task"):
        subagent_type = tool_input.get("subagent_type")
        if isinstance(subagent_type, str) and subagent_type in _TRACKED_SUBAGENT_TYPES:
            _process_loop_log.append("subagent_end", type=subagent_type)
        flag_key = _SUBAGENT_TYPE_FLAGS.get(subagent_type) if isinstance(subagent_type, str) else None
        if flag_key is not None:

            def _set_agent_flag(state: dict, flag_key: str = flag_key) -> dict | None:
                if state.get(flag_key, False):
                    return None
                state[flag_key] = True
                return state

            update_state(session_id, _set_agent_flag)
        return 0

    # mcp__codex__codex: codex-review起動検出
    # 実装用途（codex-impl経由）の呼び出しは`codex_impl_invoked`が真になっているため、
    # レビュー起動の誤記録を避けて`codex_review_invoked`を記録しない。
    if tool_name == "mcp__codex__codex":

        def _set_codex_review_invoked_via_mcp(state: dict) -> dict | None:
            if state.get("codex_impl_invoked", False):
                return None
            if state.get("codex_review_invoked", False):
                return None
            state["codex_review_invoked"] = True
            return state

        update_state(session_id, _set_codex_review_invoked_via_mcp)
        return 0

    # Read: 規範ファイル読み込みのセッション状態フラグ化
    if tool_name == "Read":
        file_path_raw = tool_input.get("file_path")
        if isinstance(file_path_raw, str):
            # Windowsからのバックスラッシュ区切りを正規化してから判定する
            file_path_normalized = file_path_raw.replace("\\", "/")
            if file_path_normalized.endswith("codex-review.md"):

                def _set_codex_review_read(state: dict) -> dict | None:
                    if state.get("codex_review_read", False):
                        return None
                    state["codex_review_read"] = True
                    return state

                update_state(session_id, _set_codex_review_read)
            if file_path_normalized.endswith("writing-standards/references/textlint-violations.md"):

                def _set_textlint_violations_read(state: dict) -> dict | None:
                    if state.get("textlint_violations_read", False):
                        return None
                    state["textlint_violations_read"] = True
                    return state

                update_state(session_id, _set_textlint_violations_read)
            if file_path_normalized.endswith("plan-mode/references/plan-file-guidelines.md"):

                def _set_plan_file_guidelines_read(state: dict) -> dict | None:
                    if state.get("plan_file_guidelines_read", False):
                        return None
                    state["plan_file_guidelines_read"] = True
                    return state

                update_state(session_id, _set_plan_file_guidelines_read)
        return 0

    # Write / Edit / MultiEdit: ファイル編集はgit log確認状態を全エントリリセットする
    # （cwd別判定の細粒度は維持せず、編集後は全cwdの再確認を要求する）。
    if tool_name in ("Write", "Edit", "MultiEdit"):

        def _reset_log(state: dict) -> dict | None:
            log_state = state.get("git_log_checked", False)
            if isinstance(log_state, dict):
                if not log_state:
                    return None
                state["git_log_checked"] = {}
                return state
            if log_state:
                state["git_log_checked"] = False
                return state
            return None

        update_state(session_id, _reset_log)
        # plan file形式検査: ~/.claude/plans/直下の.mdのみ対象。
        # plan-modeスキル未呼び出し時はPreToolUse側の警告で先行催促済みのため、
        # 構造検査をスキップして二重警告を避ける。
        state = read_state(session_id)
        file_path_raw = tool_input.get("file_path")
        file_path = file_path_raw if isinstance(file_path_raw, str) else ""
        if is_plan_file(file_path):
            # 現在の計画ファイルパスを記録する。
            # pretooluse.py側で`agent_doc_validator_invoked`の条件付き必須化を判定する際、
            # 対象ファイル一覧の内容確認のため計画ファイルを再読み込みする用途に使う。

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
        # 計画ファイル向け通知: 形式検査違反（plan-mode起動時のみ）と、
        # Write成功時の書き込み後チェック案内（plan-mode起動時のみ）を1つのadditionalContextへまとめる。
        # 状態フラグは追加せず、案内のみを一方向で通知する（詳細は
        # skills/plan-mode/references/plan-file-write-checks.md「書き込み後チェック」節）。
        if state.get("plan_mode_skill_invoked", False) and is_plan_file(file_path):
            cwd_raw = payload.get("cwd", "")
            cwd = cwd_raw if isinstance(cwd_raw, str) else ""
            messages: list[str] = []
            violations = _check_plan_format(file_path, cwd, session_id)
            if violations:
                messages.append(
                    _llm_notice(
                        f"plan file {file_path} does not conform to the expected structure."
                        f" {' '.join(violations)}"
                        f" Fix the structure per skills/plan-mode/references/plan-file-guidelines.md"
                        f" (read it first if not yet).",
                        tag="warn",
                    )
                )
            if tool_name == "Write":
                messages.append(
                    _llm_notice(
                        f"plan file {file_path} was written. Run the post-write checks:"
                        f" `uv run --script agent-toolkit/skills/plan-mode/scripts/check_plan_file.py"
                        f" {file_path}`."
                        f" See skills/plan-mode/references/plan-file-write-checks.md for details.",
                        tag="notice",
                    )
                )
            if messages:
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PostToolUse",
                                "additionalContext": "\n".join(messages),
                            }
                        },
                        ensure_ascii=False,
                    )
                )
        return 0

    # Bash以外はここで終了
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0

    # 環境変数代入接頭辞（`LOCALAPPDATA=...`等）を除去してから検出パターンを適用する。
    command = _strip_env_assignments(command)

    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""

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

        # Git状態確認の検出（status / log / diff）
        if not state.get("git_status_checked", False) and any(
            event.subcommand in _GIT_STATUS_SUBCOMMANDS for event in git_events
        ):
            state["git_status_checked"] = True
            changed = True

        # git_log_checked: log で記録、commit / rebase / push でリセット。
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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- plugin が破損して編集できなくなる事故を避けるため
        traceback.print_exc()
        sys.exit(0)
