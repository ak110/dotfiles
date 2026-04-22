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
   - `.example` / `.sample` 拡張子は検査対象外
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
import subprocess
import sys
import tempfile
import traceback

# U+FFFD (REPLACEMENT CHARACTER): UTF-8 デコード失敗の典型的な代替文字
_REPLACEMENT_CHAR = "\ufffd"

# LLM 宛てメッセージの共通プレフィックス / サフィックス。
# 詳細は skills/writing-standards/references/claude-hooks.md を参照。
_MESSAGE_PREFIX = "[auto-generated: agent-toolkit/pretooluse]"
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM 宛てメッセージを標準プレフィックス / サフィックス付きで整形する。

    `tag` に `warn` 等を渡すとプレフィックスに並置する (`[auto-generated: ...][warn]`)。
    """
    prefix = f"{_MESSAGE_PREFIX}[{tag}]" if tag else _MESSAGE_PREFIX
    return f"{prefix} {body} {_MESSAGE_SUFFIX}"


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
        cwd_raw = payload.get("cwd", "")
        cwd = cwd_raw if isinstance(cwd_raw, str) else ""
        result = _check_bash_git_commit(command, session_id, cwd)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
            return 0
        # git log --decorate 自動付与
        result = _check_bash_git_log_decorate(command, tool_input)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
            return 0
        # codex exec 未決事項の念押し
        result = _check_bash_codex_exec(command)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
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
    """U+FFFD (mojibake) を検出したら True を返す。"""
    for field, value in fields:
        position = value.find(_REPLACEMENT_CHAR)
        if position == -1:
            continue
        start = max(0, position - 10)
        end = min(len(value), position + 11)
        sample = value[start:end]
        print(
            _llm_notice(f"blocked: U+FFFD (mojibake) detected in {tool_name}.{field}. Context: {sample!r}"),
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
            _llm_notice(
                f"blocked: LF-only content detected in {tool_name}.{field}."
                f" PowerShell 5.1 cannot parse .ps1 files with LF line endings; CRLF is required."
                f" Use the Edit tool for existing files (it preserves CRLF transparently)."
                f" For new files, write via Bash with a UTF-8 BOM and CRLF line endings"
                f" (e.g., printf '\\xEF\\xBB\\xBF' > file.ps1 && ... | sed 's/$/\\r/' >> file.ps1)."
                f" Target: {file_path}"
            ),
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
    ("uv.lock", re.compile(r"(^|/)uv\.lock$"), "Use `uv add` to add dependencies and `uv remove` to remove them."),
    (
        "pnpm-lock.yaml",
        re.compile(r"(^|/)pnpm-lock\.yaml$"),
        "Use `pnpm add` to add dependencies and `pnpm remove` to remove them.",
    ),
    ("package-lock.json", re.compile(r"(^|/)package-lock\.json$"), "Use `npm install <pkg>` to add dependencies."),
    ("yarn.lock", re.compile(r"(^|/)yarn\.lock$"), "Use `yarn add` to add dependencies."),
    ("Cargo.lock", re.compile(r"(^|/)Cargo\.lock$"), "Use `cargo add` to add dependencies."),
    ("mise.lock", re.compile(r"(^|/)mise\.lock$"), "Use `mise use` / `mise install` for tool management."),
    (
        ".venv/",
        re.compile(r"(^|/)\.venv/"),
        "Do not edit virtual environment files directly; rebuild with uv or similar.",
    ),
    (
        "node_modules/",
        re.compile(r"(^|/)node_modules/"),
        "node_modules is a generated directory; do not edit it directly.",
    ),
)


def _check_lockfiles(tool_name: str, file_path: str) -> bool:
    """Lockfile や生成物ディレクトリへの直接編集を検出したら True を返す。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _LOCKFILE_RULES:
        if pattern.search(normalized):
            print(
                _llm_notice(f"blocked: direct edit of {label} is prohibited by {tool_name}. {hint} Target: {file_path}"),
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
            _llm_notice(
                f"blocked: direct edit of secret / key files is prohibited by {tool_name}."
                f" Accidental edits can cause service outages or data leaks. Target: {file_path}"
            ),
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
            "For [project.dependencies] / [project.optional-dependencies],"
            " use `uv add` / `uv remove` (to keep uv.lock in sync)."
            " For [tool.*] or version edits, proceed as-is."
        ),
    ),
    (
        "package.json",
        re.compile(r"(^|/)package\.json$"),
        (
            "For dependency edits, use `pnpm add` / `pnpm remove`"
            " (to keep pnpm-lock.yaml in sync). For scripts or metadata edits, proceed as-is."
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
                _llm_notice(
                    f"editing {label} via {tool_name}. {hint}",
                    tag="warn",
                ),
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
                _llm_notice(
                    f"home directory absolute path ({home}) detected in {tool_name}.{field}."
                    f" In version-controlled files, use `~`, `$HOME`, or `pathlib.Path.home()`"
                    f" instead to avoid environment-dependent paths."
                    f" Context: {sample!r}",
                    tag="warn",
                ),
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
        _llm_notice(
            f"blocked: {op}."
            f" Run `git log --oneline --decorate` first to confirm commit state before amend/rebase"
            f" (especially, do NOT amend/rebase commits that have already been pushed)."
        ),
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


_GIT_COMMIT_INCLUDE_WORKTREE_PATTERN = re.compile(r"(?:^|\s)(?:-\w*a\w*|--all)\b")


def _is_docs_only_commit(command: str, cwd: str) -> bool:
    """コミット対象のファイルが全て Markdown なら True を返す。

    プロジェクト方針として docs-only 変更では手動テストを省略し
    pre-commit 側の markdownlint / textlint に委ねる運用が存在する
    (本 dotfiles 含む)。その場合に未検証警告を抑制する。

    `git commit -a` / `--all` 等のコマンドでは作業ツリー側の変更も対象となるため、
    staged と working tree を切り分けて判定する。
    `cwd` 不在や git 呼び出し失敗時は False を返し従来どおり警告する (安全側)。
    """
    if not cwd:
        return False
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None:
        return False
    tail = command[match.end() :]
    for delimiter in (";", "|", "&&"):
        pos = tail.find(delimiter)
        if pos != -1:
            tail = tail[:pos]
    include_working_tree = _GIT_COMMIT_INCLUDE_WORKTREE_PATTERN.search(tail) is not None
    args = ["git", "diff", "--name-only", "HEAD"] if include_working_tree else ["git", "diff", "--cached", "--name-only"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, cwd=cwd, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    files = [line for line in result.stdout.splitlines() if line.strip()]
    if not files:
        return False
    return all(path.lower().endswith(".md") for path in files)


def _check_bash_git_commit(command: str, session_id: str, cwd: str) -> dict | None:
    """テスト未実行のまま git commit する場合に警告 JSON を返す。

    テスト実行済み (state の test_executed が true) ならスキップ。
    状態ファイル不在時は test_executed = false として扱い警告を出す。
    コミット対象が全て Markdown ファイルの場合は pre-commit 側に検証を委ねる運用を想定しスキップする。
    """
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None or not _likely_real_command(command, match.start()):
        return None
    state = _read_session_state(session_id)
    if state.get("test_executed", False):
        return None
    if _is_docs_only_commit(command, cwd):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": _llm_notice(
                "committing without running tests. Follow the verify-then-commit procedure in agent.md and run tests first.",
                tag="warn",
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
            "additionalContext": _llm_notice(
                "submitting plan file to codex review."
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
