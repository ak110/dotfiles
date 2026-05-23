#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録とplan file形式検査。

Bash / Write / Edit / MultiEdit / Skillの実行後にイベントを検出し、
セッション状態ファイルに記録する。
PreToolUseやStopフックが参照して警告・提案の判定に使う。

検出対象:

1. テスト実行 (Bash)
2. Git状態確認 (Bash) とgit log確認状態のリセット (commit/rebase/push/編集後)
3. plan file（`~/.claude/plans/*.md`）形式検査 (Write / Edit / MultiEdit)
4. plan-modeスキル呼び出し検出 (Skill)
"""

import json
import pathlib
import re
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _bash_command_parser import extract_git_events  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
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

# --- git関連サブコマンドの分類 ---

# `git status` / `git log` / `git diff` のいずれかを実行した場合に状態確認済みとみなす。
_GIT_STATUS_SUBCOMMANDS: frozenset[str] = frozenset({"status", "log", "diff"})

# git_log_checked をリセットするサブコマンド（既存コミットを書き換える・送出する系統）。
_GIT_LOG_RESET_SUBCOMMANDS: frozenset[str] = frozenset({"commit", "rebase", "push"})

# --- plan-modeスキル呼び出し検出 ---

# Skillツールの`skill`引数として許容するスキル名。
# ユーザーが手動で短縮名を渡すケースに備えてフルネームと短縮名の両方を許容する。
_PLAN_MODE_SKILL_NAMES = frozenset({"agent-toolkit:plan-mode", "plan-mode"})

# --- plan file形式検査の定数 ---

# 期待セクション一覧のSSOTは`skills/plan-mode/references/plan-file-guidelines.md`の「セクション構成と記述要件」節。
# plan-file-guidelines.md側を更新する場合は本定数も同期すること（SSOTテスト`TestPlanFormatSsot`で検査）。
_PLAN_REQUIRED_H2 = (
    "変更履歴",
    "背景",
    "対応方針",
    "調査結果",
    "変更内容",
    "実行方法",
    "進捗ログ",
    "計画ファイル（本ファイル）のパス",
)


def _is_plan_file(file_path: str) -> bool:
    """`~/.claude/plans/`直下のplan file（`*.md`）の場合に真を返す。

    `.review.md` / `.codex.log`は副次ファイルのため除外する。
    サブディレクトリ配下のファイルは対象外（直下のみ）。
    """
    if not file_path:
        return False
    try:
        path = pathlib.Path(file_path).resolve()
        plans_dir = (pathlib.Path.home() / ".claude" / "plans").resolve()
        rel = path.relative_to(plans_dir)
    except (OSError, ValueError):
        return False
    if len(rel.parts) != 1:
        return False
    name = rel.parts[0]
    if name.endswith(".review.md") or name.endswith(".codex.log"):
        return False
    return name.endswith(".md")


# コードフェンス開始/終了の判定に使う（CommonMark準拠で字種と長さを保持）。
_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})")


def _extract_h2_sections(content: str) -> list[str]:
    """Markdown本文からH2見出しのテキストを順に抽出する。

    以下の領域内の`## `行は見出しとして扱わない。

    - ファイル先頭のYAMLフロントマター（`---`または`...`で閉じる）
    - コードフェンス（開きフェンスと同字種・同長以上の閉じフェンスで抜ける。ネスト対応）
    - 複数行にまたがるHTMLコメント（`<!--`から`-->`まで）
    """
    headings: list[str] = []
    lines = content.splitlines()
    i = 0
    # フロントマター: 1 行目が `---` のときのみ検出対象とする (途中の `---` は区切り線)
    if lines and lines[0].rstrip() == "---":
        i = 1
        while i < len(lines):
            if lines[i].rstrip() in ("---", "..."):
                i += 1
                break
            i += 1

    fence_marker: str | None = None  # 開きフェンスのマーカー文字列（同字種・同長以上で閉じる）
    in_html_comment = False
    while i < len(lines):
        line = lines[i]
        i += 1
        if in_html_comment:
            # 閉じタグ到達行は `-->` 以降を解析せず丸ごとスキップする (素朴な実装)
            if "-->" in line:
                in_html_comment = False
            continue
        if fence_marker is not None:
            stripped = line.strip()
            if (
                stripped
                and stripped[0] == fence_marker[0]
                and len(stripped) >= len(fence_marker)
                and set(stripped) == {fence_marker[0]}
            ):
                fence_marker = None
            continue
        fence_match = _FENCE_PATTERN.match(line.lstrip())
        if fence_match:
            fence_marker = fence_match.group(1)
            continue
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:  # 複数行コメントの開始
            in_html_comment = True
            continue
        if line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


def _check_plan_format(file_path: str) -> list[str]:
    """Plan fileの構成を検査して違反メッセージの一覧を返す。

    検出する違反:

    - 必須H2の欠落
    - 必須H2の順序違反
    - 予期せぬH2

    読み取り失敗時は空リストを返す。
    """
    try:
        content = pathlib.Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    headings = _extract_h2_sections(content)
    allowed = set(_PLAN_REQUIRED_H2)
    violations: list[str] = []

    unexpected = [h for h in headings if h not in allowed]
    if unexpected:
        violations.append(f"unexpected H2 sections: {unexpected}. Allowed: {list(_PLAN_REQUIRED_H2)}.")

    missing = [h for h in _PLAN_REQUIRED_H2 if h not in headings]
    if missing:
        violations.append(f"missing required H2 sections: {missing}.")

    # 順序違反: 必須 H2 だけを抽出したときの順序が規定順と一致するか
    present_required = [h for h in headings if h in _PLAN_REQUIRED_H2]
    expected_order = [h for h in _PLAN_REQUIRED_H2 if h in headings]
    if present_required != expected_order:
        violations.append(
            f"required H2 sections are out of order."
            f" Expected order among present: {expected_order}, but found: {present_required}."
        )

    return violations


def main() -> int:
    """エントリポイント。exit codeは常に0。"""
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

    # Skill: plan-modeスキル呼び出し検出
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:

            def _set_invoked(state: dict) -> dict | None:
                if state.get("plan_mode_skill_invoked", False):
                    return None
                state["plan_mode_skill_invoked"] = True
                return state

            update_state(session_id, _set_invoked)
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
        if state.get("plan_mode_skill_invoked", False) and _is_plan_file(file_path):
            violations = _check_plan_format(file_path)
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

        return state if changed else None

    update_state(session_id, _apply_bash_updates)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- plugin が破損して編集できなくなる事故を避けるため
        traceback.print_exc()
        sys.exit(0)
