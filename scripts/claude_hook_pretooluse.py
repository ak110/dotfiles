#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code PreToolUse フック: dotfiles 個人環境専用チェック集。

mojibake (U+FFFD) / PowerShell LF-only 書き込みのチェックは Claude Code plugin
`agent-toolkit` へ移管した (`plugins/agent-toolkit/scripts/pretooluse.py`)。
本スクリプトは dotfiles 個人環境でのみ必要な、汎用性の低いチェックをまとめる
(他人に配布する `agent-toolkit` には含めにくい個人環境前提のチェック群)。

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
   - LF/CRLF の改行チェックは `agent-toolkit` プラグインが担当する。
3. 個人用 / ローカル専用ファイル言及検出 (warn / 非ブロック)
   - 対象パターン:
     - `CLAUDE.local.md` (リポジトリ管理外のローカルメモ)
     - ファイル名に `___` (アンダースコア3つ) を含むトークン (個人メモの慣習)
   - 他のリポジトリ管理ファイルから参照すると、無視指定などを通じてファイル名自体が
     コミットに漏れる原因になる。一方で配布対象ドキュメントで利用者に同名ファイル作成を
     推奨する文脈など、正当な言及もある。文脈依存のため最終判断は LLM に委ね、
     hook は緩い警告のみを出してブロックはしない
   - `file_path` 自体が対象パターンに一致するファイルの場合は、
     ファイル自身の作成・編集として警告もスキップする

検査対象は「新規に書き込まれる側」 (`content` / `new_string`) のみ。
`old_string` は既存内容の修正・削除を妨げないため検査しない。

出力契約:

- block: exit 2 + stderr にブロック理由を出力
- warn (allow + メッセージ): exit 0 + stdout に JSON (hookSpecificOutput.additionalContext) を出力
- 通過 (違反なし / スキップ対象ツール / 想定外入力): exit 0、出力なし

メッセージは英語で記述する (ユーザーの日本語思考コンテキストへのノイズ混入を避けるため)。
予期せぬ例外は 0 にフォールバックする (フックが破損して編集できなくなる事故を避けるため)。
"""

import json
import pathlib
import re
import sys
import traceback

_CLAUDE_LOCAL_MD = "CLAUDE.local.md"

# LLM 宛てメッセージの共通プレフィックス / サフィックス。
# 詳細は plugins/agent-toolkit/skills/claude-meta-rules/references/claude-hooks.md を参照。
_MESSAGE_PREFIX = "[auto-generated: pretooluse]"
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

    # --- warn 系 check (allow + additionalContext) ---
    result = _check_personal_file_mentions(tool_name, fields, file_path)
    if result is not None:
        print(json.dumps(result))

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
        _llm_notice(
            f"{tool_name}: blocked direct edit under ~/.claude/."
            f" This is a chezmoi deploy target and will be overwritten on next `chezmoi apply`."
            f" Edit `.chezmoi-source/dot_claude/` instead. Target: {file_path}"
        ),
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
    LF/CRLF 改行のチェックは `agent-toolkit` プラグイン側で実施しているため重複させない。
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
                _llm_notice(
                    f"{tool_name}.{field}: missing required PowerShell directives: "
                    f"{', '.join(missing)}. For Windows PowerShell 5.1 compatibility, add "
                    f"`Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'` "
                    f"near the top (within first {_PS1_DIRECTIVES_HEAD_LINES} lines, at line start)."
                    f" Target: {file_path}"
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- 個人用 / ローカル専用ファイル言及 check (warn) ---

# ファイル名に連続アンダースコア (3〜7 文字) を含むトークンを検出する。
# 個人用メモの慣習として使われるファイル名パターン。
# 8 文字以上の連続アンダースコアは区切り線等の装飾用途とみなし対象外にする。
# `[^\W_]` (= [a-zA-Z0-9]) でアンダースコア列の前後を非アンダースコアの word 文字に
# 限定し、`(?!_)` で列が 7 文字を超えないことを保証する。
# `\b` でword境界に固定することで、トークンの内側の部分マッチを避ける。
_TRIPLE_UNDERSCORE_PATTERN = re.compile(r"\b\w*[^\W_]_{3,7}(?!_)[^\W_]\w*\b")


def _check_personal_file_mentions(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> dict | None:
    """個人用 / ローカル専用ファイルの言及を検出したら allow + additionalContext を返す (warn)。

    対象は `CLAUDE.local.md` と、ファイル名に `___` を含むトークン。
    対象ファイル自身の編集は作成・更新として警告をスキップする。
    文脈依存の判断は LLM に委ね、hook は緩い警告のみを出してブロックはしない。
    """
    messages: list[str] = []
    if not _is_claude_local_md(file_path):
        for field, value in fields:
            if _CLAUDE_LOCAL_MD in value:
                messages.append(f"'{_CLAUDE_LOCAL_MD}' in {tool_name}.{field}")
                break
    if not _has_triple_underscore_filename(file_path):
        for field, value in fields:
            match = _TRIPLE_UNDERSCORE_PATTERN.search(value)
            if match:
                messages.append(f"'{match.group()}' (filename-like token containing '___') in {tool_name}.{field}")
                break
    if not messages:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": _llm_notice(
                "detected possible mention(s) of local-only personal file(s): "
                + "; ".join(messages)
                + ". Such files are typically gitignored personal memos."
                " Referencing them from version-controlled files is often unintentional"
                " and risks leaking the filename via ignore-lists or stale references."
                " On the other hand, recommending end users to create their own local memo"
                " file (e.g., in distributed docs/skills) is legitimate."
                " Judge the context and keep the mention only if it is intentional"
                " (warning only, not blocked).",
                tag="warn",
            ),
        },
    }


def _is_claude_local_md(file_path: str) -> bool:
    """ファイルパス自体が CLAUDE.local.md かを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    # パス区切りを正規化してファイル名を取得
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return name == _CLAUDE_LOCAL_MD


# 3〜7 文字の連続アンダースコアを検出するパターン (ファイル名判定用)。
_SHORT_UNDERSCORE_RUN = re.compile(r"(?<!_)_{3,7}(?!_)")


def _has_triple_underscore_filename(file_path: str) -> bool:
    """ファイル名自体に 3〜7 文字の連続アンダースコアが含まれるかを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return bool(_SHORT_UNDERSCORE_RUN.search(name))


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースは stderr に出す
        traceback.print_exc()
        sys.exit(0)
