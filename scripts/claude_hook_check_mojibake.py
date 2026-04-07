#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code PreToolUse フック: 文字化け (U+FFFD) 書き込みを検出してブロックする。

Claude Code が Write/Edit/MultiEdit 実行時に U+FFFD (replacement character) を含む内容を
書き込もうとした場合、stderr に検出箇所を出力し exit 2 で操作をブロックする。
Claude は stderr の内容を受け取って次の判断に使う。

検査対象は「新規に書き込まれる側」のフィールドのみ。
`old_string` 系は既存の文字化けを修復する正当な操作を妨げないため検査しない。

参考: <https://nyosegawa.com/posts/claude-code-mojibake-workaround/>
"""

import json
import sys

# U+FFFD (REPLACEMENT CHARACTER): UTF-8 デコード失敗の典型的な代替文字
_REPLACEMENT_CHAR = "\ufffd"


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

    violation = _find_violation(tool_name, tool_input)
    if violation is None:
        sys.exit(0)

    field, sample = violation
    print(
        f"[claude-hook-check-mojibake] {tool_name}.{field} に U+FFFD (文字化け) を検出したためブロックしました。"
        f" 周辺: {sample!r}",
        file=sys.stderr,
    )
    sys.exit(2)


def _find_violation(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    """新規書き込みフィールドを走査し、U+FFFD を含む最初の位置を返す。"""
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
    # その他のツールは対象外
    return None


def _check_field(field: str, value: object) -> tuple[str, str] | None:
    """単一フィールド値を検査し、U+FFFD を含めば (field, 周辺抜粋) を返す。"""
    if not isinstance(value, str) or _REPLACEMENT_CHAR not in value:
        return None
    position = value.index(_REPLACEMENT_CHAR)
    start = max(0, position - 10)
    end = min(len(value), position + 11)
    return field, value[start:end]


if __name__ == "__main__":
    _main()
