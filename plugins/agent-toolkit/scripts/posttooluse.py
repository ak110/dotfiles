#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code plugin agent-toolkit: PostToolUse セッション状態記録と plan file 形式検査。

Bash / Write / Edit / MultiEdit / Skill の実行後にイベントを検出し、
セッション状態ファイルに記録する。
PreToolUse や Stop フックが参照して警告・提案の判定に使う。

検出対象:

1. テスト実行 — pytest / make test / pyfltr / npm test / cargo test 等 (Bash)
2. Git 状態確認 — git status / git log / git diff (Bash)
3. codex exec resume — codex レビューの再実行 (Bash)
4. git log 確認状態のリセット — git commit / rebase / push (Bash),
   ファイル編集 (Write / Edit / MultiEdit) の実行後にリセットし、
   amend / rebase 前に改めて git log を確認させる
5. plan file (``~/.claude/plans/*.md``) 形式検査 (Write / Edit / MultiEdit)
   必須H2 の欠落・順序違反・想定外 H2を
   ``additionalContext`` で LLM に通知する (warn のみで exit code は 0 のまま)。
   セッション状態の ``plan_mode_skill_invoked`` が真の場合のみ実行する
   (未呼び出し時は PreToolUse 側で plan-mode スキル先行呼び出しを促すため、
   構造検査の二重警告を避ける)
6. plan-mode スキル呼び出し検出 (Skill) — ``agent-toolkit:plan-mode`` または
   ``plan-mode`` の呼び出しを観測し ``plan_mode_skill_invoked`` フラグを立てる。
   PreToolUse 側の最初ツール呼び出し警告および本フックの plan file 形式検査の
   有効化に使う

状態ファイルのパス: `{tempdir}/claude-agent-toolkit-{session_id}.json`

exit code 契約:

- exit 0: 常に 0（PostToolUse は許可判定に関与しない。サイレント記録 / warn のみ）

予期せぬ例外は 0 にフォールバックする。
"""

import contextlib
import json
import pathlib
import re
import sys
import tempfile
import traceback

# LLM 宛てメッセージの共通プレフィックス / サフィックス。
# 詳細は skills/writing-standards/references/claude-hooks.md を参照。
_MESSAGE_PREFIX = "[auto-generated: agent-toolkit/posttooluse]"
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM 宛てメッセージを標準プレフィックス / サフィックス付きで整形する。

    `tag` に `warn` 等を渡すとプレフィックスに並置する (`[auto-generated: ...][warn]`)。
    """
    prefix = f"{_MESSAGE_PREFIX}[{tag}]" if tag else _MESSAGE_PREFIX
    return f"{prefix} {body} {_MESSAGE_SUFFIX}"


# --- テスト実行検出パターン ---

_TEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+)?(?:python\s+-m\s+)?pytest\b"),
    re.compile(r"(?:^|[;&|]\s*)make\s+test\b"),
    re.compile(r"(?:^|[;&|]\s*)(?:uv\s+run\s+)?pyfltr\s+(?:run|ci|fast|agent)\b"),
    re.compile(r"(?:^|[;&|]\s*)(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b"),
    re.compile(r"(?:^|[;&|]\s*)cargo\s+test\b"),
)

# --- Git 状態確認検出パターン ---

_GIT_STATUS_PATTERN = re.compile(r"(?:^|[;&|]\s*)git\s+(?:status|log|diff)\b")

# --- git log 確認パターン ---

_GIT_LOG_PATTERN = re.compile(r"(?:^|[;&|]\s*)git\s+log\b")

# --- git log リセットパターン (commit / rebase / push) ---

_GIT_LOG_RESET_PATTERN = re.compile(r"\bgit\s+(?:commit|rebase|push)\b")

# --- codex exec resume 検出パターン ---

_CODEX_RESUME_PATTERN = re.compile(r"\bcodex\s+exec\s+resume\b")

# --- plan-mode スキル呼び出し検出 ---

# Skill ツールの ``skill`` 引数として許容するスキル名。
# ユーザーが手動で短縮名を渡すケースに備えてフルネームと短縮名の両方を許容する。
_PLAN_MODE_SKILL_NAMES = frozenset({"agent-toolkit:plan-mode", "plan-mode"})

# --- plan file 形式検査の定数 ---

# 期待セクション一覧の SSOT は `skills/plan-mode/references/plan-file-guidelines.md` の「セクション構成と記述要件」節。
# plan-file-guidelines.md 側を更新する場合は本定数も同期すること (SSOT テスト `TestPlanFormatSsot` で検査)。
_PLAN_REQUIRED_H2 = (
    "背景",
    "対応方針",
    "調査結果",
    "変更内容",
    "実行方法",
    "変更履歴",
    "計画ファイル",
)


def _is_plan_file(file_path: str) -> bool:
    """``~/.claude/plans/`` 直下の plan file (``*.md``) か判定する。

    `.review.md` / `.codex.log` は同ディレクトリの副次ファイルのため除外する。
    サブディレクトリ配下のファイルは対象外 (直下のみ)。
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


# コードフェンス開始／終了の判定に使う (CommonMark 準拠で字種と長さを保持)。
_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})")


def _extract_h2_sections(content: str) -> list[str]:
    """Markdown 本文から H2 見出しのテキストを順に抽出する。

    以下の領域内の `## ` 行は本文扱いとして無視する:

    - ファイル先頭の YAML フロントマター (`---` または `...` で閉じる)
    - コードフェンス (開きフェンスと同字種・同長以上の閉じフェンスで抜ける。ネスト対応)
    - 複数行にまたがる HTML コメント (`<!--` から `-->` まで)
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

    fence_marker: str | None = None  # 開きフェンスのマーカー文字列 (同字種・同長以上で閉じる)
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
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            in_html_comment = True
            continue
        if line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


def _check_plan_format(file_path: str) -> list[str]:
    """Plan file の構成チェック。違反メッセージの一覧を返す。

    検出する違反:

    - 必須 H2 の欠落
    - 必須 H2 の順序違反
    - 予期せぬ H2

    読み取り失敗時は空リストを返す (サイレントにスキップ、安全側)。
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


def _state_path(session_id: str) -> pathlib.Path:
    """セッション状態ファイルのパスを返す。"""
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-{session_id}.json"


def _read_state(path: pathlib.Path) -> dict:
    """状態ファイルを読む。不在・破損時はデフォルト値を返す。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_state(path: pathlib.Path, state: dict) -> None:
    """状態ファイルを書く。書き込み失敗は無視する（状態記録は best-effort）。"""
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _main() -> int:
    """エントリポイント。常に 0 を返す。"""
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

    path = _state_path(session_id)

    # Skill: plan-mode スキル呼び出し検出
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            state = _read_state(path)
            if not state.get("plan_mode_skill_invoked", False):
                state["plan_mode_skill_invoked"] = True
                _write_state(path, state)
        return 0

    # Write / Edit / MultiEdit: ファイル編集は git log 確認状態をリセットする
    if tool_name in ("Write", "Edit", "MultiEdit"):
        state = _read_state(path)
        if state.get("git_log_checked", False):
            state["git_log_checked"] = False
            _write_state(path, state)
        # plan file 形式検査: ~/.claude/plans/ 直下の .md のみ対象。
        # plan-mode スキル未呼び出し時は PreToolUse 側の警告で先行催促済みのため、
        # 構造検査をスキップして二重警告を避ける。
        file_path_raw = tool_input.get("file_path")
        file_path = file_path_raw if isinstance(file_path_raw, str) else ""
        if state.get("plan_mode_skill_invoked", False) and _is_plan_file(file_path):
            violations = _check_plan_format(file_path)
            if violations:
                message = _llm_notice(
                    f"plan file {file_path} does not conform to the expected structure."
                    f" {' '.join(violations)}"
                    f" Fix the structure per skills/plan-mode/SKILL.md.",
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

    # Bash 以外はここで終了
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0

    state = _read_state(path)
    changed = False

    # テスト実行検出
    if not state.get("test_executed", False):
        for pattern in _TEST_PATTERNS:
            if pattern.search(command):
                state["test_executed"] = True
                changed = True
                break

    # Git 状態確認検出
    if not state.get("git_status_checked", False) and _GIT_STATUS_PATTERN.search(command):
        state["git_status_checked"] = True
        changed = True

    # git log 確認状態の管理
    if _GIT_LOG_PATTERN.search(command):
        if not state.get("git_log_checked", False):
            state["git_log_checked"] = True
            changed = True
    elif _GIT_LOG_RESET_PATTERN.search(command) and state.get("git_log_checked", False):
        # commit / rebase / push は git log 確認状態をリセットする
        state["git_log_checked"] = False
        changed = True

    # codex exec resume 検出
    if _CODEX_RESUME_PATTERN.search(command):
        state["codex_resume_count"] = state.get("codex_resume_count", 0) + 1
        changed = True

    if changed:
        _write_state(path, state)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- plugin が破損して編集できなくなる事故を避けるため
        traceback.print_exc()
        sys.exit(0)
