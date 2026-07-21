"""claude-statuslineバイナリ（Rust製）のダウンロード・配置。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれる。GitHub ReleaseのCI成果物を
`~/.local/bin/`配下へ配置し、statusLine/subagentStatusLineをPython(uv run)起動から
Rustバイナリ直接起動へ置き換える。
"""

import logging
import pathlib
import sys

import httpx

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

_REPO = "ak110/dotfiles"
_ASSET_NAME = (
    "claude-statusline-x86_64-pc-windows-msvc.exe" if sys.platform == "win32" else "claude-statusline-x86_64-unknown-linux-gnu"
)
# 簡略化: `releases/latest/download/`はリポジトリ全体で共有される最新リリースを指し、
# タグプレフィックス（`statusline-v*`）を区別しない。
# 既知の限界: 将来`ak110/dotfiles`に本ツール以外のRust製ツールが増えGitHub Releaseを
# 追加すると、そのリリースが「latest」になり本アセットを含まないため404となり得る。
# 見直し契機: リポジトリ内に2つ目のRust製ツール・GitHub Release運用が追加された時点で、
# タグプレフィックスを区別できる取得手段（認証付きGitHub API等）へ切り替える。
_DOWNLOAD_URL = f"https://github.com/{_REPO}/releases/latest/download/{_ASSET_NAME}"
_INSTALL_DIR = pathlib.Path.home() / ".local" / "bin"
_INSTALL_PATH = _INSTALL_DIR / ("claude-statusline.exe" if sys.platform == "win32" else "claude-statusline")
_ETAG_PATH = _INSTALL_DIR / ".claude-statusline.etag"
_HTTP_TIMEOUT = 30.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run(client: httpx.Client | None = None) -> bool:
    """claude-statuslineバイナリを配置する。

    直リンク（`releases/latest/download/`）は`api.github.com`名前空間を経由しないため、
    未認証60回/時のREST APIレート制限（`GET /repos/{owner}/{repo}/releases/latest`相当）の
    対象外となる。`If-None-Match`条件付きリクエストで未更新時はボディ転送自体を省略し、
    べき等な取得を実現する。書き込みは`claude_common.atomic_write_bytes()`（同一ディレクトリの
    一時ファイル経由の原子的置換）を使い、権限設定・書き込み途中で失敗しても既存の実行可能な
    バイナリを破損状態へ置換しない。

    Args:
        client: テスト注入用。省略時は既定タイムアウトの`httpx.Client`を生成する。

    Returns:
        新規ダウンロードを行った場合True。304（未更新）または失敗時はFalse。
    """
    owns_client = client is None
    active_client = client or httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)
    try:
        _INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        headers = {}
        prev_etag = _ETAG_PATH.read_text(encoding="utf-8").strip() if _ETAG_PATH.exists() else None
        if prev_etag and _INSTALL_PATH.exists():
            headers["If-None-Match"] = prev_etag
        response = active_client.get(_DOWNLOAD_URL, headers=headers)
        if response.status_code == 304:
            logger.info(log_format.format_status("statusline", f"最新版を利用中 ({_INSTALL_PATH})"))
            return False
        response.raise_for_status()
        mode = None if sys.platform == "win32" else 0o755
        if not claude_common.atomic_write_bytes(_INSTALL_PATH, response.content, mode=mode, tag="statusline"):
            return False
        etag = response.headers.get("etag")
        if etag:
            claude_common.atomic_write_text(_ETAG_PATH, etag, tag="statusline")
        logger.info(log_format.format_status("statusline", f"インストール完了: {_INSTALL_PATH}"))
        return True
    except Exception as e:  # noqa: BLE001
        logger.info(log_format.format_status("statusline", f"バイナリ取得に失敗（statusLineは空表示になる）: {e}"))
        return False
    finally:
        if owns_client:
            active_client.close()


if __name__ == "__main__":
    main()
