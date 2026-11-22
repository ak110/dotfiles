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
# 区切りの後の改行は、コロンの次の行に引用符で始まる値がある場合（整形済みのJSONなど）だけ値の前置きとして読む。
# 値の無い代入（`TOKEN=`）の次の行は別のログ行であり、伏せると原因の行を失う。
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
# URLのuserinfoはパスワードの有無にかかわらず伏せる（トークンをユーザー名欄へ置く形式があるため）。
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
    """保存前の診断本文から資格情報を伏せる。改行と非秘匿の内容は保つ。

    名前の付かない長い文字列は伏せない。session識別子やcommit OIDを失うと原因を追跡できなくなるため。
    """
    text = _PRIVATE_KEY.sub(REDACTED, text)
    text = _AUTHORIZATION.sub(rf'\1"{REDACTED}"', text)
    text = _NAMED_SECRET.sub(rf'\1"{REDACTED}"', text)
    text = _SCHEME_CREDENTIAL.sub(rf"\1{REDACTED}", text)
    text = _URL_USERINFO.sub(rf"\1{REDACTED}@", text)
    return _QUERY_CREDENTIAL.sub(rf"\1{REDACTED}", text)


def redact_diagnostic_values(value: Any) -> Any:
    """診断の構造を保ち、資格情報名の文字列値は名前の文脈を失う前に伏せる。"""
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
    """診断全体を伏せ、行を制限してから、指定された行と区切りの文字列を返す。

    行を選ぶ関数には、上限より後ろにある診断語も判定できるよう、伏せ字済みの行の全文を渡す。
    """
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
    """改行を含むバイト予算に入る連続した末尾の診断行を返す。"""
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
    """有界な末尾の抜粋。`source_bytes`は元の本文の量で、抜粋が空になった理由の判別に使う。"""

    text: str
    omitted: bool
    source_bytes: int


def _overflow_excerpt(source_bytes: int) -> DiagnosticExcerpt:
    note = (
        f"（出力が{source_bytes}バイトで保持の上限{RETAINED_BYTES}バイトを超えたため、秘匿を確定できず本文を転載しない。"
        "全文は元の出力先で確認してください）"
    )
    return DiagnosticExcerpt(note, True, source_bytes)


class DiagnosticTail:
    """出力を受け取った順に保持し、全体を伏せてから末尾を抜粋する。

    `unit`は上限の単位で、"bytes"はUTF-8のバイト数、"chars"は文字数を表す。
    保持の上限（`RETAINED_BYTES`）を超えた出力は、全体を伏せられないため本文を転載しない。
    """

    def __init__(self, limit: int, *, unit: Literal["bytes", "chars"] = "bytes") -> None:
        self.limit = limit
        self.unit = unit
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
        if self._total > RETAINED_BYTES:
            return _overflow_excerpt(self._total)
        safe = redact_secrets(self._buffer.decode("utf-8", errors="replace"))
        if self.unit == "chars":
            return DiagnosticExcerpt(safe[-self.limit :] if self.limit else "", len(safe) > self.limit, self._total)
        encoded = safe.encode()
        text = encoded[-self.limit :].decode(errors="ignore") if self.limit else ""
        return DiagnosticExcerpt(text, len(encoded) > self.limit, self._total)


def read_diagnostic_tail(stream: BinaryIO, *, limit: int) -> DiagnosticExcerpt:
    """ファイル全体を伏せてから末尾をUTF-8バイト数で抜粋する。保持の上限を超えるファイルは読まない。"""
    size = stream.seek(0, 2)
    if size > RETAINED_BYTES:
        return _overflow_excerpt(size)
    stream.seek(0)
    tail = DiagnosticTail(limit)
    while chunk := stream.read(65536):
        tail.feed(chunk)
    return tail.excerpt()
