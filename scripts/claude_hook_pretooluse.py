#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code PreToolUse フック: Write/Edit/MultiEdit 実行前の各種チェックを統合。

複数のチェックを 1 つのサブプロセスで走らせることで、フック起動コストを削減する。
いずれか 1 つでも違反を検出したら stderr に理由を出して exit 2 でブロックする。

統合しているチェック:

1. 文字化け (U+FFFD) 検出 — 旧 `claude_hook_check_mojibake.py`
2. `.ps1` / `.ps1.tmpl` への LF-only 書き込み検出 — 旧 `claude_hook_check_ps1_eol.py`
   - Windows PowerShell 5.1 は LF 改行の `.ps1` を正しくパースできないため CRLF を強制
3. `CLAUDE.local.md` 言及検出
   - `CLAUDE.local.md` はリポジトリ管理外のローカルメモであり、
     他のリポジトリ管理ファイルからの参照は厳禁
   - ただし `file_path` 自体が `CLAUDE.local.md` の場合は正当な編集として通す

検査対象は「新規に書き込まれる側」 (`content` / `new_string`) のみ。
`old_string` は既存内容の修正・削除を妨げないため検査しない。
"""

import json
import sys

# U+FFFD (REPLACEMENT CHARACTER): UTF-8 デコード失敗の典型的な代替文字
_REPLACEMENT_CHAR = "\ufffd"
_CLAUDE_LOCAL_MD = "CLAUDE.local.md"


def _main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        # 想定外入力ではフックを無効化 (実処理を壊さない安全側)
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    # Write/Edit/MultiEdit 以外は全スキップ
    fields = _collect_new_fields(tool_name, tool_input)
    if fields is None:
        sys.exit(0)

    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""

    # 1. mojibake
    for field, value in fields:
        position = value.find(_REPLACEMENT_CHAR)
        if position == -1:
            continue
        start = max(0, position - 10)
        end = min(len(value), position + 11)
        sample = value[start:end]
        print(
            f"[pretooluse] {tool_name}.{field} に U+FFFD (文字化け) を検出したためブロックしました。 周辺: {sample!r}",
            file=sys.stderr,
        )
        sys.exit(2)

    # 2. PS1 EOL (対象拡張子のみ)
    if _is_ps1(file_path):
        for field, value in fields:
            if "\n" not in value:
                continue
            if "\r\n" in value:
                continue
            print(
                f"[pretooluse] {tool_name}.{field} に LF 改行のみの内容を検出したためブロックしました。"
                f" PowerShell 5.1 は LF 改行の .ps1 を正しくパースできないため CRLF (\\r\\n) にしてください。"
                f" 対象: {file_path}",
                file=sys.stderr,
            )
            sys.exit(2)

    # 3. CLAUDE.local.md 言及 (対象ファイル自身の編集は除外)
    if not _is_claude_local_md(file_path):
        for field, value in fields:
            if _CLAUDE_LOCAL_MD in value:
                print(
                    f"[pretooluse] {tool_name}.{field} に '{_CLAUDE_LOCAL_MD}' への言及を検出したためブロックしました。"
                    f" CLAUDE.local.md はローカル専用ファイルであり、リポジトリ管理されるファイルから"
                    f" 参照してはいけません。",
                    file=sys.stderr,
                )
                sys.exit(2)

    sys.exit(0)


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


def _is_ps1(file_path: str) -> bool:
    """対象拡張子か判定する (`.ps1` / `.ps1.tmpl`)。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _is_claude_local_md(file_path: str) -> bool:
    """ファイルパス自体が CLAUDE.local.md かを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    # パス区切りを正規化してファイル名を取得
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return name == _CLAUDE_LOCAL_MD


if __name__ == "__main__":
    _main()
