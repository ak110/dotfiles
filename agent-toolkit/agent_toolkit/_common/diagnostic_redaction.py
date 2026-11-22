"""診断として保存する外部由来の本文から資格情報を伏せる。

接続先や子プロセスが返すエラー本文・標準エラーは、要求に含めた認証値や環境変数を反射しうる。
診断ログ・終了診断・失敗診断ファイルへ保存する処理は、保存の直前でこの処理を通す。
エラーコード、例外型、接続先、session識別子、commit OID、パスは原因調査に使うため保持する。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, BinaryIO, Literal, NamedTuple

REDACTED = "[REDACTED]"

_VALUE = r"""(?:\\"(?:\\.|[^"\\])*\\"|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]&]+)"""


_SEPARATOR = r"""(?:[ \t]*:\s*(?=\\?["'])|[ \t]*[:=][ \t]*)"""
_AUTHORIZATION = re.compile(
    r"""(\b(?:proxy[-_])?authorization(?:\\?["'])?""" + _SEPARATOR + r""")"""
    r"""(?:\\"(?:\\.|[^"\\])*\\"|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|"""
    r"""(?:(?:Bearer|Basic|Digest|Negotiate)[ \t]+)?[^\s,;}\]&]+)""",
    re.IGNORECASE,
)
_NAMED_SECRET = re.compile(
    r"""(\b[\w.-]*(?:api[ _-]?key|token|secret|password|passwd)[\w.-]*(?:\\?["'])?""" + _SEPARATOR + r""")""" + _VALUE,
    re.IGNORECASE,
)
_SECRET_NAME = re.compile(
    r"(?:(?:proxy[-_])?authorization|[\w.-]*(?:api[ _-]?key|token|secret|password|passwd)[\w.-]*)", re.IGNORECASE
)
_SCHEME_CREDENTIAL = re.compile(r"(\b(?:Bearer|Basic)[ \t]+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

_URL_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/@:]*(?::[^\s/@]*)?@", re.IGNORECASE)
_QUERY_CREDENTIAL = re.compile(
    r"([?&](?:[\w.-]*(?:token|key|secret|password|passwd|signature|sig|auth|credential)[\w.-]*)=)[^&#\s\"']+",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)


def redact_secrets(text: str) -> str:
    text = _PRIVATE_KEY.sub(REDACTED, text)
    text = _AUTHORIZATION.sub(rf'\1"{REDACTED}"', text)
    text = _NAMED_SECRET.sub(rf'\1"{REDACTED}"', text)
    text = _SCHEME_CREDENTIAL.sub(rf"\1{REDACTED}", text)
    text = _URL_USERINFO.sub(rf"\1{REDACTED}@", text)
    return _QUERY_CREDENTIAL.sub(rf"\1{REDACTED}", text)


def redact_diagnostic_values(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {
            key: REDACTED
            if isinstance(key, str) and _SECRET_NAME.fullmatch(key) and isinstance(item, str)
            else redact_diagnostic_values(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_diagnostic_values(item) for item in value]
    return value


def diagnostic_excerpt(
    text: str,
    *,
    prefix_chars: int | None = None,
    prefix_bytes: int | None = None,
    tail_chars: int | None = None,
    tail_lines: int | None = None,
    collapse_whitespace: bool = False,
    omission: str = "",
) -> str:
    """未切り詰めの診断を伏せてから、指定の単位で切り取る。UTF-8の端で分断した文字は捨てる。

    `omission`は、`tail_chars`で先頭を省いた時に抜粋の前へ付ける印である（印は上限に数えない）。
    """
    safe = redact_secrets(text)
    if collapse_whitespace:
        safe = re.sub(r"\s+", " ", safe)
    if tail_lines is not None:
        safe = "\n".join(safe.strip().splitlines()[-tail_lines:]) if tail_lines else ""
    if tail_chars is not None:
        safe = (omission + safe[-tail_chars:] if tail_chars else omission) if len(safe) > tail_chars else safe
    if prefix_chars is not None:
        safe = safe[:prefix_chars]
    if prefix_bytes is not None:
        safe = safe.encode()[:prefix_bytes].decode(errors="ignore")
    return safe


def diagnostic_line_excerpt(text: str, *, line_chars: int, select: Callable[[list[str]], Sequence[int | str]]) -> list[str]:
    lines = redact_secrets(text).splitlines()
    clipped = [line[:line_chars] for line in lines]
    return [clipped[item] if isinstance(item, int) else item[:line_chars] for item in select(lines)]


def diagnostic_head(data: bytes, *, limit_bytes: int, reached_read_limit: bool) -> str:
    """先頭から有界に読んだ本文を伏せてから、UTF-8バイト数で先頭を抜粋する。

    読取の上限に達した時（`reached_read_limit`）だけ、末尾の行が認証値の途中で終わっているか判定できないため転載しない。
    上限に達しない短い読取は、その時点の出力の全体として扱う。
    """
    if reached_read_limit:
        data = data.rpartition(b"\n")[0]
    return diagnostic_excerpt(data.decode("utf-8", errors="replace").strip(), prefix_bytes=limit_bytes)


class ByteDiagnosticLine:
    """未切り詰めのログ行から、UTF-8バイト数を制限した診断行を作る。"""

    def __init__(self, text: str, *, line_bytes: int) -> None:
        self.text = diagnostic_excerpt(text, prefix_bytes=line_bytes)
        self.size = len(self.text.encode()) + 1


def newest_diagnostic_lines(lines: Sequence[ByteDiagnosticLine], *, total_bytes: int) -> list[str]:
    kept: list[str] = []
    for line in reversed(lines):
        if line.size > total_bytes:
            break
        total_bytes -= line.size
        kept.append(line.text)
    return kept[::-1]


RETAINED_BYTES = 8 * 1024 * 1024
"""全体を伏せるために保持する未加工の本文の上限。

末尾だけを保持して窓の中で秘匿の文脈を推定すると、窓より前で始まる鍵や値の文脈を失う。
そのため上限までは全体を保持して全体を伏せ、超えた本文は転載しない。
2026-11-22の観測では、管理サービスの世代の標準エラー19件は中央値217バイト、最大637KBだった。
"""


class DiagnosticExcerpt(NamedTuple):
    text: str
    omitted: bool
    source_bytes: int


def _excerpt(
    safe: str, *, limit: int, unit: Literal["bytes", "chars"], source_bytes: int, overflowed: bool
) -> DiagnosticExcerpt:
    if overflowed:
        safe = (
            f"（出力が{source_bytes}バイトで保持の上限{RETAINED_BYTES}バイトを超えたため、秘匿を確定できず本文を転載しない。"
            "全文は元の出力先で確認してください）"
        )
    if unit == "chars":
        return DiagnosticExcerpt(safe[-limit:] if limit else "", overflowed or len(safe) > limit, source_bytes)
    encoded = safe.encode()
    text = encoded[-limit:].decode(errors="ignore") if limit else ""
    return DiagnosticExcerpt(text, overflowed or len(encoded) > limit, source_bytes)


class DiagnosticTail:
    """出力を受け取った順に保持し、全体を伏せてから末尾を抜粋する。

    `unit`は上限の単位で、"bytes"はUTF-8のバイト数、"chars"は文字数を表す。
    保持の上限（`RETAINED_BYTES`）を超えた出力は、全体を伏せられないため本文を転載しない。
    """

    def __init__(self, limit: int, *, unit: Literal["bytes", "chars"] = "bytes") -> None:
        self.limit = limit
        self.unit: Literal["bytes", "chars"] = unit
        self._buffer = bytearray()
        self._total = 0

    def feed(self, chunk: bytes | str) -> None:
        data = chunk.encode() if isinstance(chunk, str) else chunk
        self._total += len(data)
        if self._total > RETAINED_BYTES:
            self._buffer.clear()
        else:
            self._buffer.extend(data)

    def excerpt(self) -> DiagnosticExcerpt:
        overflowed = self._total > RETAINED_BYTES
        safe = "" if overflowed else redact_secrets(self._buffer.decode("utf-8", errors="replace"))
        return _excerpt(safe, limit=self.limit, unit=self.unit, source_bytes=self._total, overflowed=overflowed)


def read_diagnostic_tail(stream: BinaryIO, *, limit: int) -> DiagnosticExcerpt:
    """ファイル全体を伏せてから末尾をUTF-8バイト数で抜粋する。保持の上限を超えるファイルは読まない。"""
    size = stream.seek(0, 2)
    if size > RETAINED_BYTES:
        return _excerpt("", limit=limit, unit="bytes", source_bytes=size, overflowed=True)
    stream.seek(0)
    tail = DiagnosticTail(limit)
    while chunk := stream.read(65536):
        tail.feed(chunk)
    return tail.excerpt()
