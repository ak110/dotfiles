"""ホストの役割の判定。

役割とホスト名の対応は`.chezmoi-source/.chezmoidata.toml`の`[host_roles]`が定め、chezmoiのテンプレートと本モジュールが同じ定義を読む。
ホスト名は小文字化し、最初の`.`より後ろのドメインを切り捨てた短縮名で比較する。
"""

import logging
import socket
import sys
import tomllib
from pathlib import Path

from pytools._internal import common, log_format

logger = logging.getLogger(__name__)

# dotfilesの自動更新とatk serveの常駐を担うLinuxホスト
LINUX_SERVER = "linux_server"
# media-remoteを自動起動するWindowsホスト
MEDIA_REMOTE = "media_remote"

_DATA_RELATIVE = Path(".chezmoi-source") / ".chezmoidata.toml"


def normalize_hostname(hostname: str) -> str:
    """ホスト名を小文字の短縮名へそろえる。"""
    return hostname.lower().split(".")[0]


def has_role(role: str, *, hostname: str | None = None) -> bool:
    """このホスト（`hostname`を渡した場合はそのホスト）が役割`role`を持つかを返す。

    dotfilesの作業ツリーを解決できない場合と、`.chezmoidata.toml`を読めない場合や形式が想定と異なる場合は、
    役割なし（`False`）として扱う。役割を前提とする工程は、対象外のホストと同じく何もしない。
    """
    names = _role_hostnames(role)
    return normalize_hostname(socket.gethostname() if hostname is None else hostname) in names


def is_linux_server(*, hostname: str | None = None, platform: str | None = None) -> bool:
    """Linux上で役割`linux_server`を持つホストである場合だけ真を返す。"""
    return (sys.platform if platform is None else platform) == "linux" and has_role(LINUX_SERVER, hostname=hostname)


def _role_hostnames(role: str) -> frozenset[str]:
    root = common.find_dotfiles_root()
    if root is None:
        return frozenset()
    path = root / _DATA_RELATIVE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.warning(log_format.format_status("host roles", f"{path} を読めないため役割なしとして扱う: {error}"))
        return frozenset()
    roles = data.get("host_roles")
    names = roles.get(role) if isinstance(roles, dict) else None
    if not isinstance(names, list):
        return frozenset()
    return frozenset(normalize_hostname(name) for name in names if isinstance(name, str))
