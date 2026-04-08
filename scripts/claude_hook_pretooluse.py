#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code PreToolUse フック: dotfiles 個人環境専用チェック集。

mojibake (U+FFFD) / PowerShell LF-only 書き込みのチェックは Claude Code plugin
`edit-guardrails` へ移管した (`plugins/edit-guardrails/scripts/pretooluse.py`)。
本スクリプトは dotfiles 個人環境でのみ必要な、汎用性の低いチェックをまとめる
(他人に配布する `edit-guardrails` には含めにくい個人環境前提のチェック群)。

統合しているチェック:

1. `~/.claude/` 配下への直接編集ブロック (block)
   - chezmoi の配布先のため編集しても次回 `chezmoi apply` で上書きされる。
     配布元 (`.chezmoi-source/dot_claude/`) を編集すべき。
   - 例外的に許可するサブツリー: `plans/` / `projects/` / `todos/` /
     `shell-snapshots/` / `ide/` / `statsig/` (Claude Code 自身が使うランタイム領域)
   - 例外的に許可するファイル名: `*.local.*` 系 (個人ローカル設定)
2. PowerShell スクリプトの必須ディレクティブ欠落ブロック (block, Write のみ)
   - `.ps1` / `.ps1.tmpl` の冒頭付近 (先頭 50 行以内) に
     `Set-StrictMode -Version Latest` と `$ErrorActionPreference = 'Stop'` の両方が必須。
   - CLAUDE.md の Windows PowerShell スクリプト規約を強制する。
   - Edit / MultiEdit の `new_string` はファイル先頭を含まないことが多いため対象外。
   - LF/CRLF の改行チェックは `edit-guardrails` プラグインが担当する。
3. `CLAUDE.local.md` 言及検出 (warn / 非ブロック)
   - `CLAUDE.local.md` はリポジトリ管理外のローカルファイルであり、
     他のリポジトリ管理ファイルからの参照は厳禁
   - コミット前に人間の目で気づく機会が残るため、完全ブロックはせず警告にとどめる
   - ただし `file_path` 自体が `CLAUDE.local.md` の場合は正当な編集として許可する

検査対象は「新規に書き込まれる側」 (`content` / `new_string`) のみ。
`old_string` は既存内容の修正・削除を妨げないため検査しない。

exit code 契約:

- exit 0: 通過 (違反なし / スキップ対象ツール / 想定外入力 / warn のみ)
- exit 2: block 違反検出 (stderr に理由を出力)

予期せぬ例外は 0 にフォールバックする (フックが破損して編集できなくなる事故を避けるため)。
"""

import json
import pathlib
import re
import sys
import traceback

_CLAUDE_LOCAL_MD = "CLAUDE.local.md"


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

    # Write/Edit/MultiEdit 以外は全スキップ
    fields = _collect_new_fields(tool_name, tool_input)
    if fields is None:
        return 0

    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""

    # --- block 系 check (最初の違反で exit 2) ---
    if _check_home_claude_edit(tool_name, file_path):
        return 2
    if _check_ps1_directives(tool_name, fields, file_path):
        return 2

    # --- warn 系 check (stderr に警告のみ、exit code は 0 のまま) ---
    _check_local_md_reference(tool_name, fields, file_path)

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


# --- ~/.claude/ 配下の直接編集 check (block) ---

# 例外的に編集を許可するサブツリー (Claude Code のランタイム領域 / プラン作業領域)。
# 配布対象 (rules/ や agents/) は含めない。
_HOME_CLAUDE_ALLOWED_DIRS: frozenset[str] = frozenset(
    {
        "plans",  # plan mode が書き込む計画ファイル
        "projects",  # Claude Code のセッション履歴
        "todos",  # TodoWrite ストレージ
        "shell-snapshots",  # シェル スナップショット
        "ide",  # IDE 連携キャッシュ
        "statsig",  # Statsig SDK のキャッシュ
    }
)

# 例外的に編集を許可するファイル名 (`*.local.*` 系 ローカル設定)。
_HOME_CLAUDE_ALLOWED_NAME_SUBSTRING = ".local."


def _check_home_claude_edit(tool_name: str, file_path: str) -> bool:
    """`~/.claude/` 配下への直接編集を検出したら True を返す。

    chezmoi の配布先のため、このパス配下を編集しても次回 `chezmoi apply` で
    上書きされてしまう。配布元の `.chezmoi-source/dot_claude/` を編集すべき。
    """
    if not file_path:
        return False
    try:
        target = pathlib.Path(file_path).expanduser()
        # 相対パスでは ~/.claude 配下か判定できないためスキップする
        # (resolve すると CWD 基準で解決され誤検出になり得るので resolve 前に判定)
        if not target.is_absolute():
            return False
        # `.` / `..`・シンボリックリンクを解消して字句比較の迂回を防ぐ。
        # strict=False で存在しないパスでも例外を送出しない。
        target = target.resolve(strict=False)
        home_claude = (pathlib.Path.home() / ".claude").resolve(strict=False)
    except (ValueError, OSError):
        return False
    try:
        rel = target.relative_to(home_claude)
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        # `~/.claude` そのもの (実際にはディレクトリ) は対象外
        return False
    if parts[0] in _HOME_CLAUDE_ALLOWED_DIRS:
        return False
    if _HOME_CLAUDE_ALLOWED_NAME_SUBSTRING in rel.name:
        return False
    print(
        f"[pretooluse] {tool_name}: ~/.claude/ 配下への直接編集はブロックしました。"
        f" chezmoi の配布先のため次回 `chezmoi apply` で上書きされます。"
        f" 配布元の `.chezmoi-source/dot_claude/` を編集してください。 対象: {file_path}",
        file=sys.stderr,
    )
    return True


# --- PowerShell 必須ディレクティブ check (block) ---

# 冒頭付近に必須のディレクティブ。両方が揃わなければブロックする。
# 行頭厳格マッチ (インデント不可) にすることで「コメント内に文字列が含まれるだけ」や
# 「関数/条件ブロック内に書かれている (= スクリプト全体には効かない)」ケースをブロックする。
_PS1_REQUIRED_DIRECTIVES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^Set-StrictMode\s+-Version\s+Latest\b", re.MULTILINE),
        "Set-StrictMode -Version Latest",
    ),
    (
        re.compile(r"^\$ErrorActionPreference\s*=\s*'Stop'", re.MULTILINE),
        "$ErrorActionPreference = 'Stop'",
    ),
)

# 検査する先頭行数 (コメントブロックを許容するため広めに取る)。
_PS1_DIRECTIVES_HEAD_LINES = 50


def _is_ps1(file_path: str) -> bool:
    """対象拡張子か判定する (`.ps1` / `.ps1.tmpl`)。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _check_ps1_directives(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """PowerShell スクリプトの冒頭ディレクティブ欠落を検出したら True を返す。

    Edit / MultiEdit の `new_string` はファイル先頭を含まないことが多いため Write のみを対象とする。
    LF/CRLF 改行のチェックは `edit-guardrails` プラグイン側で実施しているため重複させない。
    """
    if tool_name != "Write" or not _is_ps1(file_path):
        return False
    for field, value in fields:
        # BOM (U+FEFF) は chezmoi テンプレートで使われることがあるため除去してから判定する
        normalized = value.lstrip("\ufeff")
        head = "\n".join(normalized.splitlines()[:_PS1_DIRECTIVES_HEAD_LINES])
        missing = [label for pattern, label in _PS1_REQUIRED_DIRECTIVES if pattern.search(head) is None]
        if missing:
            print(
                f"[pretooluse] {tool_name}.{field}: PowerShell スクリプトに必須ディレクティブがありません: "
                f"{', '.join(missing)}。Windows PowerShell 5.1 互換性確保のため "
                f"`Set-StrictMode -Version Latest` と `$ErrorActionPreference = 'Stop'` を冒頭付近"
                f"(先頭 {_PS1_DIRECTIVES_HEAD_LINES} 行以内・行頭)に記述してください。 対象: {file_path}",
                file=sys.stderr,
            )
            return True
    return False


# --- CLAUDE.local.md 言及 check (warn) ---


def _check_local_md_reference(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """`CLAUDE.local.md` への言及を検出したら警告を出して True を返す (warn)。

    対象ファイル自身の編集は正当な操作として除外する。
    """
    if _is_claude_local_md(file_path):
        return False
    for field, value in fields:
        if _CLAUDE_LOCAL_MD in value:
            print(
                f"[pretooluse][warn] {tool_name}.{field} に '{_CLAUDE_LOCAL_MD}' への言及を検出しました。"
                f" CLAUDE.local.md はローカル専用ファイルであり、リポジトリ管理されるファイルから"
                f" 参照しないでください (警告のみ・ブロックはしません)。",
                file=sys.stderr,
            )
            return True
    return False


def _is_claude_local_md(file_path: str) -> bool:
    """ファイルパス自体が CLAUDE.local.md かを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    # パス区切りを正規化してファイル名を取得
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return name == _CLAUDE_LOCAL_MD


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースは stderr に出す
        traceback.print_exc()
        sys.exit(0)
