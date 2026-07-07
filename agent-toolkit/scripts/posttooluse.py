#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録とplan file形式検査。

Bash / Write / Edit / MultiEdit / Skill / Read / EnterPlanModeの実行後にイベントを検出し、
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
   （plan-reviewer / naive-executor / plan-impl-reviewer / agent-doc-validator）
9. codex-review起動検出（Skill: agent-toolkit:plan-codex-review / mcp__codex__codexツール）
10. 現在の計画ファイルパス記録 (Write / Edit / MultiEdit、plan file判定時)
    （pretooluse.py側の`agent_doc_validator_invoked`条件付き必須化判定に使用）
"""

import json
import pathlib
import re
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _bash_command_parser import extract_git_events  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import compute_prelint_hashes, is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_format import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_h2_sections,
    extract_h3_headings_under_h2,
    extract_target_files_from_changes,
    is_agent_facing_md,
)
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

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

# --- 事前lint検査の成功記録パターン ---

# scratchpad配下への計画ファイル本文事前lint検査用Bashコマンドを完全一致型で識別する。
# 改行・シェル演算子・後続文字列を許容しないことで、lint失敗を後続処理で覆す経路を防ぐ。
_PRELINT_BASH_FULLMATCH = re.compile(
    r"(?:uvx[ \t]+)?pyfltr[ \t]+run-for-agent[ \t]+"
    r"--commands=textlint,markdownlint,typos,colloquial-check[ \t]+--enable=colloquial-check[ \t]+(\S+)",
)

# check_line_width.py単独実行のBashコマンドを完全一致型で識別する。
# scratchpad配下の計画ファイル本文に対する127幅検査の成功を別キーで記録する。
_LINE_WIDTH_BASH_FULLMATCH = re.compile(
    r"(?:uvx?[ \t]+(?:run[ \t]+(?:--no-project[ \t]+)?--script[ \t]+)?|python3?[ \t]+)"
    r"\S*check_line_width\.py[ \t]+(\S+)",
)

# pyfltrの標準出力サマリー行（exit:0）を成功判定根拠とする。
_PYFLTR_SUCCESS_PATTERN = re.compile(r'"kind"\s*:\s*"summary"\s*,\s*"exit"\s*:\s*0\b')

# --- git関連サブコマンドの分類 ---

# `git status` / `git log` / `git diff` のいずれかを実行した場合に状態確認済みとみなす。
_GIT_STATUS_SUBCOMMANDS: frozenset[str] = frozenset({"status", "log", "diff"})

# git_log_checked をリセットするサブコマンド（既存コミットを書き換える・送出する系統）。
_GIT_LOG_RESET_SUBCOMMANDS: frozenset[str] = frozenset({"commit", "rebase", "push"})

# --- plan-modeスキル呼び出し検出 ---

# Skillツールの`skill`引数として許容するスキル名。
# ユーザーが手動で短縮名を渡すケースに備えてフルネームと短縮名の両方を許容する。
_PLAN_MODE_SKILL_NAMES = frozenset({"agent-toolkit:plan-mode", "plan-mode"})

# Stop hookでの振り返り誘導抑止に使う配布物側の振り返りスキル名。観測したらsession_stateへ記録する。
_SESSION_REVIEW_SKILL_NAMES = frozenset({"agent-toolkit:session-review"})

# codex-review起動検出に使うスキル名。Skillツール経由での起動を観測したらsession_stateへ記録する。
_CODEX_REVIEW_SKILL_NAMES = frozenset({"agent-toolkit:plan-codex-review"})

# AgentツールとTaskツールのsubagent_type別セッション状態フラグ記録。
# フルネームと短縮名の両方を許容する。
_SUBAGENT_TYPE_FLAGS: dict[str, str] = {
    "plan-reviewer": "plan_reviewer_invoked",
    "agent-toolkit:plan-reviewer": "plan_reviewer_invoked",
    "naive-executor": "naive_executor_invoked",
    "agent-toolkit:naive-executor": "naive_executor_invoked",
    "plan-impl-reviewer": "plan_impl_reviewer_invoked",
    "agent-toolkit:plan-impl-reviewer": "plan_impl_reviewer_invoked",
    "agent-doc-validator": "agent_doc_validator_invoked",
    "agent-toolkit:agent-doc-validator": "agent_doc_validator_invoked",
}

# --- plan file形式検査の定数 ---


def _check_target_file_line_counts(content: str, cwd: str) -> str | None:
    """対象ファイル一覧の各パスの行数を確認し、200行以上の対象種別ファイルがあれば警告メッセージを返す。"""
    paths = extract_target_files_from_changes(content)
    if not paths:
        return None
    base = pathlib.Path(cwd) if cwd else pathlib.Path.cwd()
    over_limit: list[tuple[str, int]] = []
    for rel in paths:
        if not is_agent_facing_md(rel):
            continue
        target = base / rel
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        if line_count >= 200:
            over_limit.append((rel, line_count))
    if not over_limit:
        return None
    listed = ", ".join(f"{p} ({n} lines)" for p, n in over_limit)
    return (
        f"plan file contains target files with 200 or more lines: {listed}."
        " Per agent-standards 'document size limit' section"
        " (200-219 lines is boundary-close, 220 or more is a violation),"
        " assemble the post-revision final form and measure with `wc -l`."
        " Confirm whether you have measured the final form."
    )


def _check_plan_format(file_path: str, cwd: str) -> list[str]:
    """Plan fileの構成を検査して違反メッセージの一覧を返す。

    検出する違反:

    - `## 変更内容`配下の先頭H3が「対象ファイル一覧」でない
    - `## 変更内容 > ### 対象ファイル一覧`配下の対象種別ファイルが200行以上

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
    if line_count_warning:
        violations.append(line_count_warning)

    return violations


def _record_bash_success_hash(lint_target: str, cwd: str, state: dict, state_key: str) -> bool:
    """成功したBashコマンドの対象ファイルを読み込みハッシュを`state[state_key]`へ登録する。

    ファイル読み込み失敗時、または既存ハッシュと重複する場合は登録せずFalseを返す。
    登録した場合はTrueを返す（呼び出し元でstate変更フラグに反映するため）。
    """
    lint_path = pathlib.Path(lint_target).expanduser()
    if not lint_path.is_absolute() and cwd:
        lint_path = pathlib.Path(cwd) / lint_path
    try:
        file_content = lint_path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, UnicodeDecodeError, OSError):
        return False
    passed = state.get(state_key, [])
    if not isinstance(passed, list):
        passed = []
    passed_set = set(passed)
    full_sha, stripped_sha = compute_prelint_hashes(file_content)
    if full_sha in passed_set and stripped_sha in passed_set:
        return False
    passed_set.add(full_sha)
    passed_set.add(stripped_sha)
    state[state_key] = sorted(passed_set)
    return True


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
        if isinstance(skill_name, str) and skill_name in _CODEX_REVIEW_SKILL_NAMES:

            def _set_codex_review_invoked(state: dict) -> dict | None:
                if state.get("codex_review_invoked", False):
                    return None
                state["codex_review_invoked"] = True
                return state

            update_state(session_id, _set_codex_review_invoked)
        return 0

    # AgentとTask: subagent_type別セッション状態フラグ記録
    if tool_name in ("Agent", "Task"):
        subagent_type = tool_input.get("subagent_type")
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
    if tool_name == "mcp__codex__codex":

        def _set_codex_review_invoked_via_mcp(state: dict) -> dict | None:
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
        if state.get("plan_mode_skill_invoked", False) and is_plan_file(file_path):
            cwd_raw = payload.get("cwd", "")
            cwd = cwd_raw if isinstance(cwd_raw, str) else ""
            violations = _check_plan_format(file_path, cwd)
            if violations:
                message = _llm_notice(
                    f"plan file {file_path} does not conform to the expected structure."
                    f" {' '.join(violations)}"
                    f" Fix the structure per skills/plan-mode/references/plan-file-guidelines.md (read it first if not yet).",
                    tag="warn",
                )
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PostToolUse",
                                "additionalContext": message,
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

        # 事前lint検査の成功記録: 完全一致型で許可パターン以外の余計な構文（シェル演算子・改行・後続文字列）を全て除外する
        # （lint失敗を後続処理で成功終了へ覆す形を防ぐため）
        prelint_match = _PRELINT_BASH_FULLMATCH.fullmatch(command.strip())
        if prelint_match:
            tool_response = payload.get("tool_response") or {}
            if not isinstance(tool_response, dict):
                tool_response = {}
            interrupted = bool(tool_response.get("interrupted"))
            output = tool_response.get("output") or tool_response.get("stdout") or ""
            if not isinstance(output, str):
                output = ""
            pyfltr_succeeded = bool(_PYFLTR_SUCCESS_PATTERN.search(output))
            if not interrupted and pyfltr_succeeded:
                lint_target = prelint_match.group(1).strip("'\"")
                if _record_bash_success_hash(lint_target, cwd, state, "plan_prelint_passed"):
                    changed = True

        # check_line_width.py 単独実行の成功記録: 完全一致型で識別し、終了コード0時に別キーへハッシュ登録する
        line_width_match = _LINE_WIDTH_BASH_FULLMATCH.fullmatch(command.strip())
        if line_width_match:
            tool_response = payload.get("tool_response") or {}
            if not isinstance(tool_response, dict):
                tool_response = {}
            interrupted = bool(tool_response.get("interrupted"))
            exit_code_raw = tool_response.get("exit_code")
            try:
                exit_code = int(exit_code_raw) if exit_code_raw is not None else 0
            except (TypeError, ValueError):
                exit_code = -1
            if not interrupted and exit_code == 0:
                lint_target = line_width_match.group(1).strip("'\"")
                if _record_bash_success_hash(lint_target, cwd, state, "plan_prelint_passed_line_width"):
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
