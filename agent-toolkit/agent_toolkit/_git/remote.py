"""GitリモートURLの取得と正規化を提供する。"""

from __future__ import annotations

import pathlib
import re
import subprocess
import urllib.parse

from agent_toolkit._common.next_action import ActionableError

_SCP_LIKE_REMOTE_RE = re.compile(r"^[^@\s]+@(?P<host>[^:\s]+):(?P<path>.+)$")
_NORMALIZED_REMOTE_RE = re.compile(r"[^/]+(?:/[^/]+){2,}")
_REMOTE_URL_NEXT_ACTION = (
    "`git remote get-url origin`の値を確認し、HTTPS・SSHのURLか`host/owner/repository`形式の値を指定し直す"
)


def normalize_remote_url(remote_url: str) -> str:
    """GitリモートURLを`host/owner/repository`形式へ正規化する。

    HTTPS、SSH URI、SSH短縮、正規化済み識別子を受理する。受理外は`ValueError`の派生の`ActionableError`を送出する。
    ポート番号を伴うURI（`ssh://git@host:22/owner/repo.git`等）はホスト名だけを採用し、
    ポートをパスの要素として扱わない。
    """
    value = remote_url.strip()
    if not value:
        raise ActionableError(f"リモートURLとして解析できません: {remote_url!r}", next_action=_REMOTE_URL_NEXT_ACTION)

    # スキーム付きの値はSCP短縮形の判定より先にURLとして解析する。
    # `ssh://git@host:22/owner/repo.git`はSCP短縮形の正規表現にも一致するため、
    # 判定順を誤るとポート番号がパスの先頭要素として取り込まれる。
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https", "ssh"} or parsed.hostname is None:
            raise ActionableError(f"リモートURLとして解析できません: {remote_url!r}", next_action=_REMOTE_URL_NEXT_ACTION)
        host = parsed.hostname
        path = parsed.path
    elif (scp_match := _SCP_LIKE_REMOTE_RE.fullmatch(value)) is not None:
        host = scp_match.group("host")
        path = scp_match.group("path")
    elif _NORMALIZED_REMOTE_RE.fullmatch(value) is not None and "@" not in value:
        host, path = value.split("/", maxsplit=1)
    else:
        raise ActionableError(f"リモートURLとして解析できません: {remote_url!r}", next_action=_REMOTE_URL_NEXT_ACTION)

    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not normalized_path or "/" not in normalized_path:
        raise ActionableError(f"リモートURLとして解析できません: {remote_url!r}", next_action=_REMOTE_URL_NEXT_ACTION)
    return f"{host.lower()}/{normalized_path.lower()}"


def resolve_repo_identifier(value: str) -> str | None:
    """保存済みリポジトリ識別子を正規なURL形へ解決する。

    URL形は直接正規化し、実在するローカルパスはoriginのURLを取得して正規化する。
    空値、存在しないパス、Git管理外、origin未設定、不正なURLは解決不能としてNoneを返す。
    """
    try:
        return normalize_remote_url(value)
    except ValueError:
        pass
    local_path = pathlib.Path(value).expanduser()
    try:
        if not local_path.exists():
            return None
    except OSError:
        return None
    result = subprocess.run(
        ["git", "-C", str(local_path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return normalize_remote_url(result.stdout.strip())
    except ValueError:
        return None


def canonical_repo(value: str, cache: dict[str, str | None]) -> str | None:
    """リポジトリ識別子を呼び出し単位のキャッシュを介して正規化する。"""
    if value not in cache:
        cache[value] = resolve_repo_identifier(value)
    return cache[value]
