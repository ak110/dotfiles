#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code PreToolUse フック: dotfiles 個人環境専用チェック集。

mojibake (U+FFFD) / PowerShell LF-only 書き込みのチェックは Claude Code plugin
`edit-guardrails` へ移管した (`plugins/edit-guardrails/scripts/pretooluse.py`)。
本スクリプトは dotfiles 個人環境でのみ必要な、汎用性の低いチェックをまとめる。
将来的に個人環境限定のチェックを追加する余地があるためファイル名は維持している。

統合しているチェック:

1. `CLAUDE.local.md` 言及検出 (警告のみ / 非ブロック)
   - `CLAUDE.local.md` はリポジトリ管理外のローカルメモであり、
     他のリポジトリ管理ファイルからの参照は厳禁
   - コミット前に人間の目で気づければ十分なので完全ブロックはせず警告に留める
   - ただし `file_path` 自体が `CLAUDE.local.md` の場合は正当な編集として通す

検査対象は「新規に書き込まれる側」 (`content` / `new_string`) のみ。
`old_string` は既存内容の修正・削除を妨げないため検査しない。

exit code 契約:

- 常に exit 0 (警告のみ・非ブロック)
- 想定外入力もすべて exit 0 (安全側)
"""

import json
import sys

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

    # CLAUDE.local.md 言及 (対象ファイル自身の編集は除外)
    # 警告のみで処理は通過させる (exit 0)
    if not _is_claude_local_md(file_path):
        for field, value in fields:
            if _CLAUDE_LOCAL_MD in value:
                print(
                    f"[pretooluse][warn] {tool_name}.{field} に '{_CLAUDE_LOCAL_MD}' への言及を検出しました。"
                    f" CLAUDE.local.md はローカル専用ファイルであり、リポジトリ管理されるファイルから"
                    f" 参照しないでください (警告のみ・ブロックはしません)。",
                    file=sys.stderr,
                )
                break  # 警告は 1 度だけ出せば十分

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


def _is_claude_local_md(file_path: str) -> bool:
    """ファイルパス自体が CLAUDE.local.md かを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    # パス区切りを正規化してファイル名を取得
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return name == _CLAUDE_LOCAL_MD


if __name__ == "__main__":
    _main()
