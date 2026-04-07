#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code PreToolUse フック: .ps1 / .ps1.tmpl への LF 書き込みを検出してブロックする。

Windows PowerShell 5.1 は LF 改行の `.ps1` を正しくパースできず構文エラーになる。
本リポジトリでは `.gitattributes` で `eol=crlf` を指定しているが、Claude Code の
Write/Edit/MultiEdit ツールは常に LF で書き込むため、コミット前まで気付けない。

このフックは対象ファイルの拡張子が `.ps1` または `.ps1.tmpl` の場合に、
書き込もうとしている文字列が CR (\\r) を含まない LF のみの内容なら
exit 2 でブロックする。

検査対象は「新規に書き込まれる側」のフィールドのみ (old_string 系は対象外)。
"""

import json
import sys


def _main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not _is_ps1(file_path_raw):
        sys.exit(0)

    violation = _find_violation(tool_name, tool_input)
    if violation is None:
        sys.exit(0)

    field = violation
    print(
        f"[claude-hook-check-ps1-eol] {tool_name}.{field} に LF 改行のみの内容を検出したためブロックしました。"
        f" PowerShell 5.1 は LF 改行の .ps1 を正しくパースできないため CRLF (\\r\\n) にしてください。"
        f" 対象: {file_path_raw}",
        file=sys.stderr,
    )
    sys.exit(2)


def _is_ps1(file_path: str) -> bool:
    """対象拡張子か判定する (`.ps1` / `.ps1.tmpl`)。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _find_violation(tool_name: str, tool_input: dict) -> str | None:
    """LF のみの書き込みを検出したフィールド名を返す。

    複数行を含む内容が LF のみの場合に違反とする。
    改行そのものを含まない 1 行だけの Edit は対象外 (誤検出防止)。
    """
    if tool_name == "Write":
        return _check_field("content", tool_input.get("content"))
    if tool_name == "Edit":
        return _check_field("new_string", tool_input.get("new_string"))
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return None
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                continue
            hit = _check_field(f"edits[{index}].new_string", edit.get("new_string"))
            if hit is not None:
                return hit
        return None
    return None


def _check_field(field: str, value: object) -> str | None:
    r"""フィールド値を検査し、違反なら field 名を返す。

    - 改行を含まない値は対象外 (単行 Edit の誤検出防止)
    - `\\n` を含み `\\r\\n` を含まなければ違反
    """
    if not isinstance(value, str):
        return None
    if "\n" not in value:
        return None
    if "\r\n" in value:
        return None
    return field


if __name__ == "__main__":
    _main()
