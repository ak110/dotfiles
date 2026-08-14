"""GitリモートURLの取得と正規化を提供する。"""

from __future__ import annotations

import re
import urllib.parse

_SCP_LIKE_REMOTE_RE = re.compile(r"^[^@\s]+@(?P<host>[^:\s]+):(?P<path>.+)$")
_NORMALIZED_REMOTE_RE = re.compile(r"[^/]+(?:/[^/]+){2,}")


def normalize_remote_url(remote_url: str) -> str:
    """GitリモートURLを`host/owner/repository`形式へ正規化する。

    HTTPS、SSH URI、SSH短縮、正規化済み識別子を受理する。受理外はValueErrorを送出する。
    ポート番号を伴うURI（`ssh://git@host:22/owner/repo.git`等）はホスト名だけを採用し、
    ポートを経路要素として扱わない。
    """
    value = remote_url.strip()
    if not value:
        raise ValueError(f"リモートURLとして解析できません: {remote_url!r}")

    # スキーム付きの値はSCP短縮形の判定より先にURLとして解析する。
    # `ssh://git@host:22/owner/repo.git`はSCP短縮形の正規表現にも一致するため、
    # 判定順を誤るとポート番号が経路の先頭要素として取り込まれる。
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https", "ssh"} or parsed.hostname is None:
            raise ValueError(f"リモートURLとして解析できません: {remote_url!r}")
        host = parsed.hostname
        path = parsed.path
    elif (scp_match := _SCP_LIKE_REMOTE_RE.fullmatch(value)) is not None:
        host = scp_match.group("host")
        path = scp_match.group("path")
    elif _NORMALIZED_REMOTE_RE.fullmatch(value) is not None and "@" not in value:
        host, path = value.split("/", maxsplit=1)
    else:
        raise ValueError(f"リモートURLとして解析できません: {remote_url!r}")

    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not normalized_path or "/" not in normalized_path:
        raise ValueError(f"リモートURLとして解析できません: {remote_url!r}")
    return f"{host.lower()}/{normalized_path.lower()}"
