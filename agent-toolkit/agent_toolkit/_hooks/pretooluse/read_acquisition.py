"""本文量と行範囲を、Codexで実行できる二層の取得例へ変換する。"""

from __future__ import annotations

import json
import pathlib
import shlex
from collections.abc import Sequence

# 配布設定のtool_output_token_limit内で、終了情報とJSON包絡の余裕を確保する。
_TOKEN_LIMIT = 20_000
_RESULT_MARGIN = 1024


def codex_acquisition(path: pathlib.Path, ranges: Sequence[tuple[int, int]] = ()) -> str:
    """選択行を重複なく分割し、セルごとの両層予算と結果全体の表示を案内する。

    rangesの省略は全文。指定節の最後が資料末尾でない場合は終了行を保持する。
    本文はUTF-8バイト数÷3の切上げを基準にし、JSONのエスケープ増分も含める。
    """
    with path.open("rb") as source:
        lines = list(source)
    selected: list[tuple[int, int]] = []
    for start, end in sorted(ranges or [(1, len(lines))]):
        if selected and start <= selected[-1][1] + 1:
            selected[-1] = (selected[-1][0], max(end, selected[-1][1]))
        else:
            selected.append((start, end))
    chunks: list[tuple[int, int]] = []
    constraints: list[str] = []
    size = 0
    for first, last in selected:
        start = first
        display_bytes = 0
        for number in range(first, last + 1):
            line = lines[number - 1]
            size += len(line)
            # text(result)は本文をJSON文字列として表示するため、引用符・改行等の増分を数える。
            escaped_bytes = len(json.dumps(line.decode("utf-8", errors="replace"), ensure_ascii=False).encode("utf-8")) - 2
            if (escaped_bytes + 2) // 3 + _RESULT_MARGIN > _TOKEN_LIMIT:
                if start < number:
                    chunks.append((start, number - 1))
                constraints.append(
                    f"{path}:{number}は1行だけで出力上限{_TOKEN_LIMIT}トークンを超える見積りであり、"
                    "行範囲では収まらない。必要な項目を抽出するか、保持可能な出力枠を確認して取得方法を選べ。"
                    "未取得の行は読了に含めるな。"
                )
                start, display_bytes = number + 1, 0
                continue
            if (display_bytes + escaped_bytes + 2) // 3 + _RESULT_MARGIN > _TOKEN_LIMIT:
                chunks.append((start, number - 1))
                start, display_bytes = number, 0
            display_bytes += escaped_bytes
        if start <= last:
            chunks.append((start, last))
    if not lines:
        chunks.append((1, 0))
    steps = [
        f"{size}バイト、予算目安{(size + 2) // 3}トークン。"
        "以下の各コードブロックを別々のfunctions.execとして順に実行せよ。"
        "内側コマンドと外側セルの予算は本文・JSON表示・終了情報の余裕を含む。"
        "同じセルへまとめる場合は外側max_output_tokensを内側の合計以上にせよ。"
    ]
    quoted = shlex.quote(str(path))
    for start, end in chunks:
        command = (
            f"cat -- {quoted}"
            if not ranges and len(chunks) == 1 and not constraints
            else f"sed -n '{start},{'$' if end == len(lines) else end}p' -- {quoted}"
        )
        budget = _TOKEN_LIMIT
        arguments = json.dumps({"cmd": command, "max_output_tokens": budget}, ensure_ascii=False)
        steps.append(
            f"取得コマンド: `{command}`\n```javascript\n"
            f'// @exec: {{"max_output_tokens": {budget}}}\n'
            f"const result = await tools.exec_command({arguments});\ntext(result);\n```"
        )
    steps.extend(constraints)
    steps.append(
        "session_idが返ったら同じ実行をwrite_stdinで終了まで回収し、返却全体をtext(result)で保持せよ。"
        "外側cell_idはfunctions.waitで回収せよ。省略が観測された範囲は取得済みにせず、"
        "original token countが枠に収まる場合だけ予算を増やして再取得し、収まらなければ範囲を狭めよ。"
    )
    return "\n".join(steps)
