#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code plugin edit-guardrails: PreToolUse 統合フック。

Write / Edit / MultiEdit の実行前に以下のチェックを順に走らせる。
1 プロセスで全 check を直列実行し、最初の block 違反で exit 2 する。
warn 種別の check は stderr に警告を出しつつ処理を継続する。

統合しているチェック:

1. 文字化け (U+FFFD) 検出 (block)
2. `.ps1` / `.ps1.tmpl` への LF-only 書き込み検出 (block)
   - Windows PowerShell 5.1 は LF 改行の `.ps1` を正しくパースできないため CRLF を強制
3. lockfile / 生成物ディレクトリの直接編集 (block)
   - `uv.lock`, `pnpm-lock.yaml`, `package-lock.json`, `yarn.lock`, `Cargo.lock`,
     `mise.lock`, `.venv/`, `node_modules/`
4. シークレット / 鍵ファイルの直接編集 (block)
   - `.env*`, `*.pem`, `*.key`, `.encrypt_key`, `.secret_key`, `github_action(.pub)?`
   - `.example` / `.sample` 拡張子は素通し
5. manifest ファイルの手編集 (warn)
   - `pyproject.toml`, `package.json`
6. ホームディレクトリの絶対パス混入 (warn)
   - `$HOME` を含むリテラルがリポジトリ管理ファイルに書き込まれるのを検知

検査対象は「新規に書き込まれる側」 (`content` / `new_string`) のみ。
`old_string` は既存内容の修正・削除を妨げないため検査しない。

exit code 契約:

- exit 0: 通過 (違反なし / スキップ対象ツール / 想定外入力 / warn のみ)
- exit 2: block 違反検出 (stderr に理由を出力)

予期せぬ例外は 0 にフォールバックする (plugin の hook が壊れて編集不能になる
事故を避けるため、安全側に倒している)。
"""

import json
import pathlib
import re
import sys
import traceback

# U+FFFD (REPLACEMENT CHARACTER): UTF-8 デコード失敗の典型的な代替文字
_REPLACEMENT_CHAR = "\ufffd"


def _main() -> int:
    """エントリポイント。exit code を返す (0 または 2)。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        # 想定外入力ではフックを無効化 (実処理を壊さない安全側)
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
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
    if _is_ps1(file_path) and _check_ps1_eol(tool_name, fields, file_path):
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
            f"[edit-guardrails] {tool_name}.{field} に U+FFFD (文字化け) を検出したためブロックしました。 周辺: {sample!r}",
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
            f"[edit-guardrails] {tool_name}.{field} に LF 改行のみの内容を検出したためブロックしました。"
            f" PowerShell 5.1 は LF 改行の .ps1 を正しくパースできないため CRLF (\\r\\n) にしてください。"
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
                f"[edit-guardrails] {tool_name}: {label} の直接編集は禁止です。{hint} 対象: {file_path}",
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
            f"[edit-guardrails] {tool_name}: シークレット/鍵ファイルの直接編集は禁止です。"
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
                f"[edit-guardrails] {tool_name}: {label} を編集します (警告)。{hint}",
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
                f"[edit-guardrails] {tool_name}.{field} にホームディレクトリの絶対パス ({home}) を検出しました (警告)。"
                f" リポジトリ管理ファイルでは `~` や `$HOME` / `pathlib.Path.home()` を使い、"
                f"環境依存パスが混入しないようにしてください。"
                f" 周辺: {sample!r}",
                file=sys.stderr,
            )
            return True
    return False


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- plugin が壊れて編集不能になる事故を避けるため広く捕捉
        # 予期せぬ例外は安全側 (通過) に倒す。デバッグのためスタックトレースは stderr に出す
        traceback.print_exc()
        sys.exit(0)
