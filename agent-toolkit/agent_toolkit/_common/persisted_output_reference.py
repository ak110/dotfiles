"""通知の生成側と記録の消費側で、退避案内の形式を共有する。"""

import pathlib
import re

from agent_toolkit._common import message_format

CODEX_NOTICE_REFERENCE_PREFIX = "PreToolUse通知の詳細を保存した。保存先: "
PERSISTED_OUTPUT_PREFIX = "<persisted-output>"


def reference_path(body: str) -> pathlib.Path | None:
    """既知の退避案内が持つ保存先を返す。Previewに含まれる引用は解析しない。"""
    body = body.lstrip()
    # 保存案内の先頭だけを読む。通知本文やPreview内にも同じ文言が引用され得る。
    if body.startswith(PERSISTED_OUTPUT_PREFIX):
        match = re.match(
            rf"{re.escape(PERSISTED_OUTPUT_PREFIX)}\r?\nOutput too large[^\r\n]* Full output saved to: (?P<path>[^\r\n]+)",
            body,
        )
    else:
        match = re.match(
            rf'<(?:{message_format.AUTO_ELEMENT_NAME_PATTERN})\b(?=[^>]*\bsource="pretooluse")'
            rf'(?=[^>]*\bkind="warn")[^>]*>\r?\n{re.escape(CODEX_NOTICE_REFERENCE_PREFIX)}(?P<path>[^\r\n]+)',
            body,
        )
    if match is None:
        return None
    path = pathlib.Path(match["path"].strip())
    return path if path.is_absolute() else None
