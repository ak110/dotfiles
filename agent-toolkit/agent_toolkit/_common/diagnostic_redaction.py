"""診断として保存する外部由来の本文から資格情報を伏せる。

接続先や子プロセスが返すエラー本文・標準エラーは、要求に含めた認証値や環境変数を反射しうる。
診断ログ・終了診断・失敗診断ファイルへ保存する処理は、保存の直前でこの処理を通す。
エラーコード、例外型、接続先、session識別子、commit OID、パスは原因調査に使うため保持する。
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_VALUE = r"""(?:\\"(?:\\.|[^"\\])*\\"|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]&]+)"""
_AUTHORIZATION = re.compile(
    r"""(\b(?:proxy[-_])?authorization(?:\\?["'])?\s*[:=]\s*)"""
    r"""(?:\\"(?:\\.|[^"\\])*\\"|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:(?:Bearer|Basic|Digest|Negotiate)\s+)?[^\s,;}\]&]+)""",
    re.IGNORECASE,
)
_NAMED_SECRET = re.compile(
    r"""(\b[\w.-]*(?:api[ _-]?key|token|secret|password|passwd)[\w.-]*(?:\\?["'])?\s*[:=]\s*)""" + _VALUE,
    re.IGNORECASE,
)
_SECRET_NAME = re.compile(
    r"(?:(?:proxy[-_])?authorization|[\w.-]*(?:api[ _-]?key|token|secret|password|passwd)[\w.-]*)", re.IGNORECASE
)
_SCHEME_CREDENTIAL = re.compile(r"(\b(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
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
