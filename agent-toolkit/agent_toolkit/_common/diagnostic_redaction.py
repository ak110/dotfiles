"""診断として保存する外部由来の本文から資格情報を伏せる。

接続先や子プロセスが返すエラー本文・標準エラーは、要求に含めた認証値や環境変数を反射しうる。
診断ログ・終了診断・失敗診断ファイルへ保存する処理は、保存の直前でこの処理を通す。
エラーコード、例外型、接続先、session識別子、commit OID、パスは原因調査に使うため保持する。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, BinaryIO

REDACTED = "[REDACTED]"

_VALUE = r"""(?:\\"(?:\\.|[^"\\])*\\"|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]&]+)"""
_AUTHORIZATION = re.compile(
    r"""(\b(?:proxy[-_])?authorization(?:\\?["'])?[ \t]*[:=][ \t]*)"""
    r"""(?:\\"(?:\\.|[^"\\])*\\"|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|"""
    r"""(?:(?:Bearer|Basic|Digest|Negotiate)[ \t]+)?[^\s,;}\]&]+)""",
    re.IGNORECASE,
)
_NAMED_SECRET = re.compile(
    r"""(\b[\w.-]*(?:api[ _-]?key|token|secret|password|passwd)[\w.-]*(?:\\?["'])?[ \t]*[:=][ \t]*)""" + _VALUE,
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


def diagnostic_tail_window(limit: int) -> int:
    """出力上限の倍まで読み、先頭行を除外しても末尾を残せる余裕を持たせる。"""
    return limit * 2


def diagnostic_tail(data: bytes | bytearray, *, limit: int) -> tuple[str, bool]:
    """有界な末尾窓を伏せてからUTF-8バイト数を制限し、省略の有無を返す。

    窓より長い入力は1バイト余分に受け取り、窓が途中から始まることを識別する。
    窓の先頭行は認証値の途中か判定できないため、行末まで転載しない。
    """
    window = diagnostic_tail_window(limit)
    omitted = len(data) > window
    if omitted:
        _, _, data = data[-window:].partition(b"\n")
        # 開始行が窓の外にある秘密鍵は、終了行までの全体を転載しない。
        end = re.search(rb"-----END [A-Z0-9 ]*PRIVATE KEY-----", data)
        if end and not re.search(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", data[: end.start()]):
            _, _, data = data[end.end() :].partition(b"\n")
    safe = redact_secrets(data.decode("utf-8", errors="replace")).encode()
    return safe[-limit:].decode(errors="ignore"), omitted or len(safe) > limit


def read_diagnostic_tail(stream: BinaryIO, *, limit: int) -> tuple[str, bool]:
    """ファイル全体を読まず、上限の倍と境界判定用の1バイトだけを読む。"""
    window = diagnostic_tail_window(limit) + 1
    size = stream.seek(0, 2)
    stream.seek(max(0, size - window))
    return diagnostic_tail(stream.read(window), limit=limit)
