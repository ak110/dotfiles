#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code plugin agent-toolkit: PreToolUse 統合フック。

Write / Edit / MultiEdit / Bash の実行前に以下のチェックを順に実行する。
block 系 check は 1 プロセスで直列実行し、最初の違反で exit 2 する。
warn 種別の check は stderr に警告を出しつつ処理を継続する。

統合しているチェック:

1. 文字化け (U+FFFD) 検出 (block, Write/Edit/MultiEdit)
2. `.ps1` / `.ps1.tmpl` への LF-only 書き込み検出 (block, Write/Edit/MultiEdit)
   - Windows PowerShell 5.1 は LF 改行の `.ps1` を正しくパースできないため CRLF を強制
3. lockfile / 生成物ディレクトリの直接編集 (block, Write/Edit/MultiEdit)
   - `uv.lock`, `pnpm-lock.yaml`, `package-lock.json`, `yarn.lock`, `Cargo.lock`,
     `mise.lock`, `.venv/`, `node_modules/`
4. シークレット / 鍵ファイルの直接編集 (block, Write/Edit/MultiEdit)
   - `.env*`, `*.pem`, `*.key`, `.encrypt_key`, `.secret_key`, `github_action(.pub)?`
   - `.example` / `.sample` 拡張子は素通し
5. manifest ファイルの手編集 (warn, Write/Edit/MultiEdit)
   - `pyproject.toml`, `package.json`
6. ホームディレクトリの絶対パス混入 (warn, Write/Edit/MultiEdit)
   - `$HOME` を含むリテラルがリポジトリ管理ファイルに書き込まれるのを検知

block 系 check の検査対象は「新規に書き込まれる側」 (`content` / `new_string`)
のみ。`old_string` は既存内容の修正・削除を妨げないため検査しない。

exit code 契約:

- exit 0: 通過 (違反なし / スキップ対象ツール / 想定外入力 / warn のみ)
- exit 2: block 違反検出 (stderr に理由を出力)

予期せぬ例外は 0 にフォールバックする (plugin の hook が破損して編集できなくなる
事故を避けるため、安全側の判定としている)。
"""

import json
import pathlib
import re
import sys
import tempfile
import traceback

# U+FFFD (REPLACEMENT CHARACTER): UTF-8 デコード失敗の典型的な代替文字
_REPLACEMENT_CHAR = "\ufffd"


def _main() -> int:
    """エントリポイント。exit code を返す (0 または 2)。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        # 想定外入力ではフックを無効化 (実処理の破損を避ける安全側の判定)
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    # Bash は専用ハンドラ
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return 0
        session_id = payload.get("session_id", "")
        # git amend / rebase は直前に git log を確認していなければブロック
        if _check_bash_amend_rebase_without_log(command, session_id):
            return 2
        # git commit 未検証警告
        result = _check_bash_git_commit(command, session_id)
        if result is not None:
            print(json.dumps(result))
            return 0
        # git log --decorate 自動付与
        result = _check_bash_git_log_decorate(command, tool_input)
        if result is not None:
            print(json.dumps(result))
            return 0
        # codex exec 未決事項の念押し
        result = _check_bash_codex_exec(command)
        if result is not None:
            print(json.dumps(result))
            return 0
        return 0

    # Write/Edit/MultiEdit 以外は全スキップ
    fields = _collect_new_fields(tool_name, tool_input)
    if fields is None:
        return 0

    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""

    # --- block 系 check (最初の違反で exit 2) ---
    if _check_mojibake(tool_name, fields):
        return 2
    # Edit/MultiEdit は内部的に CRLF を透過的に維持するためチェック不要。
    # Write のみ LF で書き込むため EOL チェックを実行する。
    if tool_name == "Write" and _is_ps1(file_path) and _check_ps1_eol(tool_name, fields, file_path):
        return 2
    if _check_lockfiles(tool_name, file_path):
        return 2
    if _check_secrets(tool_name, file_path):
        return 2

    # --- warn 系 check (stderr に警告のみ、exit code は 0 のまま) ---
    _check_manifest(tool_name, file_path)
    _check_home_path(tool_name, fields, file_path)

    return 0


def _collect_new_fields(tool_name: str, tool_input: dict) -> list[tuple[str, str]] | None:
    """対象ツールの「新規書き込みフィールド」を (field 名, 値) のリストで返す。

    対象外ツールの場合は None を返す。値が文字列でないものはスキップする。
    """
    if tool_name == "Write":
        value = tool_input.get("content")
        return [("content", value)] if isinstance(value, str) else []
    if tool_name == "Edit":
        value = tool_input.get("new_string")
        return [("new_string", value)] if isinstance(value, str) else []
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return []
        result: list[tuple[str, str]] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                continue
            new_string = edit.get("new_string")
            if isinstance(new_string, str):
                result.append((f"edits[{index}].new_string", new_string))
        return result
    return None


def _check_mojibake(tool_name: str, fields: list[tuple[str, str]]) -> bool:
    """U+FFFD (文字化け) を検出したら True を返す。"""
    for field, value in fields:
        position = value.find(_REPLACEMENT_CHAR)
        if position == -1:
            continue
        start = max(0, position - 10)
        end = min(len(value), position + 11)
        sample = value[start:end]
        print(
            f"[agent-toolkit] {tool_name}.{field} に U+FFFD (文字化け) を検出したためブロックしました。 周辺: {sample!r}",
            file=sys.stderr,
        )
        return True
    return False


def _check_ps1_eol(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """PowerShell スクリプトへの LF-only 書き込みを検出したら True を返す。"""
    for field, value in fields:
        if "\n" not in value:
            continue
        if "\r\n" in value:
            continue
        print(
            f"[agent-toolkit] {tool_name}.{field} に LF 改行のみの内容を検出したためブロックしました。"
            f" PowerShell 5.1 は LF 改行の .ps1 を正しくパースできないため CRLF が必要です。"
            f" Edit ツールは CRLF を透過的に維持するため、既存ファイルの編集には Edit を使ってください。"
            f" 新規ファイル作成時は Bash ツールで BOM 付き CRLF ファイルを書いてください"
            f" (例: printf '\\xEF\\xBB\\xBF' > file.ps1 && ... | sed 's/$/\\r/' >> file.ps1)。"
            f" 対象: {file_path}",
            file=sys.stderr,
        )
        return True
    return False


def _is_ps1(file_path: str) -> bool:
    """対象拡張子か判定する (`.ps1` / `.ps1.tmpl`)。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


# --- lockfile / 生成物ディレクトリ check ---

# (label, regex, hint) のタプル。regex は file_path 全体に対するマッチ。
_LOCKFILE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("uv.lock", re.compile(r"(^|/)uv\.lock$"), "依存追加は `uv add`、削除は `uv remove` を使ってください。"),
    ("pnpm-lock.yaml", re.compile(r"(^|/)pnpm-lock\.yaml$"), "依存追加は `pnpm add`、削除は `pnpm remove` を使ってください。"),
    ("package-lock.json", re.compile(r"(^|/)package-lock\.json$"), "依存追加は `npm install <pkg>` を使ってください。"),
    ("yarn.lock", re.compile(r"(^|/)yarn\.lock$"), "依存追加は `yarn add` を使ってください。"),
    ("Cargo.lock", re.compile(r"(^|/)Cargo\.lock$"), "依存追加は `cargo add` を使ってください。"),
    ("mise.lock", re.compile(r"(^|/)mise\.lock$"), "ツール管理は `mise use` / `mise install` を使ってください。"),
    (".venv/", re.compile(r"(^|/)\.venv/"), "仮想環境の中身は直接編集せず、uv などで再構築してください。"),
    ("node_modules/", re.compile(r"(^|/)node_modules/"), "node_modules は生成物なので直接編集しないでください。"),
)


def _check_lockfiles(tool_name: str, file_path: str) -> bool:
    """Lockfile や生成物ディレクトリへの直接編集を検出したら True を返す。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _LOCKFILE_RULES:
        if pattern.search(normalized):
            print(
                f"[agent-toolkit] {tool_name}: {label} の直接編集は禁止です。{hint} 対象: {file_path}",
                file=sys.stderr,
            )
            return True
    return False


# --- シークレット / 鍵ファイル check ---

_SECRETS_PATTERN = re.compile(
    r"(^|/)("
    r"\.env(\..+)?"
    r"|\.encrypt_key"
    r"|\.secret_key"
    r"|github_action(\.pub)?"
    r"|[^/]+\.(pem|key)"
    r")$"
)

_SECRETS_EXEMPT_SUFFIXES: tuple[str, ...] = (".example", ".sample", "-example", "-sample")


def _check_secrets(tool_name: str, file_path: str) -> bool:
    """シークレット/鍵ファイルへの直接編集を検出したら True を返す。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if normalized.endswith(_SECRETS_EXEMPT_SUFFIXES):
        return False
    if _SECRETS_PATTERN.search(normalized):
        print(
            f"[agent-toolkit] {tool_name}: シークレット/鍵ファイルの直接編集は禁止です。"
            f" 誤編集はサービス停止や情報漏洩につながります。対象: {file_path}",
            file=sys.stderr,
        )
        return True
    return False


# --- manifest 手編集 check (warn) ---

_MANIFEST_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "pyproject.toml",
        re.compile(r"(^|/)pyproject\.toml$"),
        (
            "[project.dependencies] / [project.optional-dependencies] の編集なら"
            " `uv add` / `uv remove` を使ってください (uv.lock 更新漏れ防止)。"
            "[tool.*] や version などの編集はそのまま続行して構いません。"
        ),
    ),
    (
        "package.json",
        re.compile(r"(^|/)package\.json$"),
        (
            "依存関係の編集なら `pnpm add` / `pnpm remove` を使ってください"
            " (pnpm-lock.yaml 更新漏れ防止)。scripts や metadata の編集はそのまま続行して構いません。"
        ),
    ),
)


def _check_manifest(tool_name: str, file_path: str) -> bool:
    """Manifest 手編集を検出したら警告を出して True を返す (warn なので exit code は変えない)。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _MANIFEST_RULES:
        if pattern.search(normalized):
            print(
                f"[agent-toolkit] {tool_name}: {label} を編集します (警告)。{hint}",
                file=sys.stderr,
            )
            return True
    return False


# --- ホームディレクトリパス混入 check (warn) ---

# 混入を許容するファイル末尾パターン (ローカル設定やログなど)
_HOME_PATH_SKIP_SUFFIXES: tuple[str, ...] = (
    ".local.md",
    ".local.json",
    ".local.yaml",
    ".local.yml",
    ".local.toml",
    ".jsonl",
    ".log",
)


def _check_home_path(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """ホームディレクトリの絶対パス混入を検出したら警告を出して True を返す。

    リポジトリ管理ファイルに `/home/user/...` のような環境依存パスが書き込まれると
    他環境での再現性が崩れるため警告する。警告のみで edit は継続 (warn)。
    """
    home_str = str(pathlib.Path.home())
    # ルートなど極端に短いパスは誤検出を避けてスキップ
    if len(home_str) < 3:
        return False

    normalized_path = file_path.replace("\\", "/")
    if normalized_path.endswith(_HOME_PATH_SKIP_SUFFIXES):
        return False
    if normalized_path.endswith("/CLAUDE.local.md") or normalized_path == "CLAUDE.local.md":
        return False
    if normalized_path.endswith("/.claude/settings.local.json"):
        return False

    # POSIX 正規化された両表記で検査 (Windows から POSIX 風パスが混入するケースに対応)
    candidates = {home_str, home_str.replace("\\", "/")}

    for field, value in fields:
        for home in candidates:
            position = value.find(home)
            if position == -1:
                continue
            start = max(0, position - 20)
            end = min(len(value), position + len(home) + 20)
            sample = value[start:end]
            print(
                f"[agent-toolkit] {tool_name}.{field} にホームディレクトリの絶対パス ({home}) を検出しました (警告)。"
                f" リポジトリ管理ファイルでは `~` や `$HOME` / `pathlib.Path.home()` を使い、"
                f"環境依存パスが混入しないようにしてください。"
                f" 周辺: {sample!r}",
                file=sys.stderr,
            )
            return True
    return False


# --- Bash: heredoc 内のパターンを除外するヘルパー ---


def _likely_real_command(command: str, pos: int) -> bool:
    """マッチ位置がシェルコマンド文脈にあるかヒューリスティックで判定する。

    heredoc (<<) がマッチ位置より前にある場合、マッチはリテラル文字列の
    一部である可能性が高いため False を返す。
    python3 -c / cat << 等でファイル内容を書き込むケースの誤検出を防ぐ。
    """
    prefix = command[:pos]
    return "<<" not in prefix


# --- Bash: git amend / rebase を log 未確認でブロック ---

_GIT_AMEND_PATTERN = re.compile(r"\bgit\s+commit\b.*--amend\b")
_GIT_REBASE_PATTERN = re.compile(r"\bgit\s+rebase\b")


def _check_bash_amend_rebase_without_log(command: str, session_id: str) -> bool:
    """Git commit --amend / git rebase を git log 未確認で実行しようとした場合にブロックする。

    amend / rebase は既存コミットを書き換えるため、直前に git log --decorate で
    コミット状態（特にプッシュ済みかどうか）を確認する必要がある。
    ユーザーが裏で push している場合もあるため、
    ファイル編集・commit・rebase・push・Stop を挟むと確認状態はリセットされる。
    """
    amend_match = _GIT_AMEND_PATTERN.search(command)
    rebase_match = _GIT_REBASE_PATTERN.search(command)
    is_amend = amend_match is not None and _likely_real_command(command, amend_match.start())
    is_rebase = rebase_match is not None and _likely_real_command(command, rebase_match.start())
    if not is_amend and not is_rebase:
        return False
    state = _read_session_state(session_id)
    if state.get("git_log_checked", False):
        return False
    op = "git commit --amend" if is_amend else "git rebase"
    print(
        f"[agent-toolkit] {op} をブロックしました。"
        f"amend / rebase の前に `git log --oneline --decorate` で"
        f"コミット状態を確認してください"
        f"（特にプッシュ済みコミットへの amend / rebase は厳禁です）。",
        file=sys.stderr,
    )
    return True


# --- Bash: git commit 未検証警告 ---

_GIT_COMMIT_PATTERN = re.compile(r"\bgit\s+commit\b")


def _session_state_path(session_id: str) -> pathlib.Path:
    """セッション状態ファイルのパスを返す (posttooluse.py と共通のパス規則)。"""
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-{session_id}.json"


def _read_session_state(session_id: str) -> dict:
    """セッション状態を読む。不在・破損時は空辞書を返す。"""
    if not isinstance(session_id, str) or not session_id:
        return {}
    try:
        return json.loads(_session_state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _check_bash_git_commit(command: str, session_id: str) -> dict | None:
    """テスト未実行のまま git commit する場合に警告 JSON を返す。

    テスト実行済み (state の test_executed が true) ならスキップ。
    状態ファイル不在時は test_executed = false として扱い警告を出す。
    """
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None or not _likely_real_command(command, match.start()):
        return None
    state = _read_session_state(session_id)
    if state.get("test_executed", False):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": (
                "[agent-toolkit] committing without running tests."
                " Follow the verify-then-commit procedure in agent.md and run tests first."
            ),
        },
    }


# --- Bash: git log --decorate 自動付与 ---

_GIT_LOG_PATTERN = re.compile(r"\bgit\s+log\b")


def _check_bash_git_log_decorate(command: str, tool_input: dict) -> dict | None:
    """Git log に --decorate がない場合、自動で挿入した updatedInput を返す。

    複合コマンド (セミコロン・パイプ結合) 内の git log にも対応する。
    git log から次のセミコロン・パイプ・行末までの範囲に --decorate が
    含まれるかを判定する。
    """
    match = _GIT_LOG_PATTERN.search(command)
    if match is None or not _likely_real_command(command, match.start()):
        return None
    # git log から次のセミコロン・パイプ・行末までのスコープを取得
    rest = command[match.start() :]
    scope_end = len(rest)
    for delimiter in (";", "|", "&&"):
        pos = rest.find(delimiter)
        if pos != -1 and pos < scope_end:
            scope_end = pos
    scope = rest[:scope_end]
    if "--decorate" in scope:
        return None
    # git log を git log --decorate に 1 箇所だけ置換
    updated_command = command[: match.end()] + " --decorate" + command[match.end() :]
    updated_input = dict(tool_input)
    updated_input["command"] = updated_command
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        },
        "systemMessage": "[agent-toolkit] automatically inserted --decorate into git log.",
    }


# --- Bash: codex exec 未決事項の念押し ---

_CODEX_EXEC_PATTERN = re.compile(r"\bcodex\s+exec\b")
_CODEX_RESUME_PATTERN_PRE = re.compile(r"\bcodex\s+exec\s+resume\b")


def _check_bash_codex_exec(command: str) -> dict | None:
    """Codex exec (resume 以外) を検出した場合に未決事項確認の念押しを返す。"""
    exec_match = _CODEX_EXEC_PATTERN.search(command)
    if exec_match is None or not _likely_real_command(command, exec_match.start()):
        return None
    if _CODEX_RESUME_PATTERN_PRE.search(command):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": (
                "[agent-toolkit] submitting plan file to codex review."
                " Pre-submission check: are there any decisions made by assumption"
                " rather than user confirmation?"
                " Resolve any open questions with the user before proceeding."
            ),
        },
    }


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- plugin が破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースは stderr に出す
        traceback.print_exc()
        sys.exit(0)
