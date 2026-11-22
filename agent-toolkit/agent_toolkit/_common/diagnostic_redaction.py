"""診断として保存する外部由来の本文から資格情報を伏せる。

接続先や子プロセスが返すエラー本文・標準エラーは、要求に含めた認証値や環境変数を反射しうる。
診断ログ・終了診断・失敗診断ファイルへ保存する処理は、保存の直前でこの処理を通す。
エラーコード、例外型、接続先、session識別子、commit OID、パスは原因調査に使うため保持する。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, BinaryIO, NamedTuple

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
) -> str:
    """未切り詰めの診断を伏せてから、指定の単位で切り取る。UTF-8の端で分断した文字は捨てる。"""
    safe = redact_secrets(text)
    if collapse_whitespace:
        safe = re.sub(r"\s+", " ", safe)
    if tail_lines is not None:
        safe = "\n".join(safe.strip().splitlines()[-tail_lines:]) if tail_lines else ""
    if tail_chars is not None:
        safe = safe[-tail_chars:] if tail_chars else ""
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


def diagnostic_head(data: bytes, *, limit: int, complete: bool) -> str:
    """先頭から有界に読んだ本文を伏せてから文字数を制限する。

    読み残しがある時（`complete`が偽）は、末尾の行が認証値の途中で終わっているか判定できないため転載しない。
    """
    if not complete:
        data = data.rpartition(b"\n")[0]
    return diagnostic_excerpt(data.decode("utf-8", errors="replace").strip(), prefix_chars=limit)


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


_KEY_MARKER = re.compile(rb"-----(BEGIN|END) [A-Z0-9 ]*PRIVATE KEY-----")
_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
_MARKER_CARRY = 1024
"""捨てた範囲の末尾から次の走査へ持ち越す量。見出し行は改行を含まないため、最後の改行より後ろだけを持ち越す。"""


def _scan_key_markers(carry: bytes, data: bytes, inside: bool) -> tuple[bool, bytes]:
    """`redact_secrets`の秘密鍵の規則と同じ順で見出しを数え、鍵の内側かと次の持ち越しを返す。"""
    text = carry + data
    for marker in _KEY_MARKER.finditer(text):
        if marker.end() <= len(carry):
            continue  # 持ち越し分だけで完結する見出しは前回数えた。
        if marker.group(1) == b"BEGIN":
            inside = True
        elif inside:
            inside = False
    return inside, text[text.rfind(b"\n") + 1 :][-_MARKER_CARRY:]


class DiagnosticExcerpt(NamedTuple):
    """有界な末尾の抜粋。`source_bytes`は元の本文の量で、抜粋が空になった理由の判別に使う。"""

    text: str
    omitted: bool
    source_bytes: int


class DiagnosticTail:
    """バイト列の末尾を上限の倍まで保持し、捨てた範囲が秘密鍵の途中で終わったかを持ち越す。

    窓の外で始まり終端の無い秘密鍵は、窓の中だけでは通常の行と区別できないため、捨てる時点の状態を残す。
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._window = limit * 2
        self._buffer = bytearray()
        self._carry = b""
        self._inside_key = False
        self._total = 0

    def feed(self, chunk: bytes) -> None:
        self._total += len(chunk)
        self._buffer.extend(chunk)
        excess = len(self._buffer) - self._window
        if excess > 0:
            dropped = bytes(self._buffer[:excess])
            del self._buffer[:excess]
            self._inside_key, self._carry = _scan_key_markers(self._carry, dropped, self._inside_key)

    def excerpt(self) -> DiagnosticExcerpt:
        """保持した窓を伏せてからUTF-8バイト数を制限する。

        窓が途中から始まる時は、認証値の途中か判定できない先頭行を転載しない。
        """
        data = bytes(self._buffer)
        omitted = self._total > len(data)
        inside = False
        if omitted:
            head, _, data = data.partition(b"\n")
            inside, _ = _scan_key_markers(self._carry, head, self._inside_key)
        text = data.decode("utf-8", errors="replace")
        if inside:
            end = _KEY_END.search(text)
            text = REDACTED + (text[end.end() :] if end else "")
        safe = redact_secrets(text).encode()
        return DiagnosticExcerpt(safe[-self.limit :].decode(errors="ignore"), omitted or len(safe) > self.limit, self._total)


def read_diagnostic_tail(stream: BinaryIO, *, limit: int) -> DiagnosticExcerpt:
    """ファイルを先頭から順に読み、保持する量を上限の倍に抑えて末尾を抜粋する。

    窓より前の秘密鍵の状態を判定するため全体を走査する。読んだ範囲は保持しない。
    """
    tail = DiagnosticTail(limit)
    while chunk := stream.read(65536):
        tail.feed(chunk)
    return tail.excerpt()
