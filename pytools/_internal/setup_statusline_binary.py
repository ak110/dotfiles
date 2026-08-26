"""claude-statuslineバイナリ（Rust製）のダウンロード・配置。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれる。GitHub ReleaseのCI成果物を
`~/.local/bin/`配下へ配置し、statusLine/subagentStatusLineをPython(uv run)起動から
Rustバイナリ直接起動へ置き換える。
"""

import logging
import os
import pathlib
import subprocess
import sys

import httpx

from pytools._internal import claude_common, log_format, setup_mise

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
_DOWNLOAD_URL_ENV = "DOTFILES_STATUSLINE_DOWNLOAD_URL"
_STATUSLINE_DIR = pathlib.Path("rust/claude-statusline")
_STATUSLINE_MANIFEST = _STATUSLINE_DIR / "Cargo.toml"
_BUILD_DIR = _STATUSLINE_DIR / "target" / "release"
_GIT_TIMEOUT = 30.0
_BUILD_TIMEOUT = 600.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run(client: httpx.Client | None = None) -> bool:
    """claude-statuslineバイナリを配置する。

    `CHEZMOI_WORKING_TREE`が`develop`のGit作業ツリーを指し、
    `origin/master`との差分がある場合は、解決済みmiseからstatuslineをビルドする。
    それ以外の場合はGitHub Releaseから取得する。開発版のビルドまたは配置に失敗した場合は
    例外を送出し、`post_apply`がステップ失敗として記録できるようにする。
    """
    working_tree = _find_development_tree()
    if working_tree is not None:
        return _install_development_binary(working_tree)
    return _download_release(client)


def _find_development_tree() -> pathlib.Path | None:
    """ローカルstatuslineを使うGit作業ツリーを返す。"""
    working_tree_value = os.environ.get("CHEZMOI_WORKING_TREE")
    if not working_tree_value:
        return None
    working_tree = pathlib.Path(working_tree_value)

    repository = _run_git(working_tree, ["rev-parse", "--show-toplevel"])
    if repository is None or repository.returncode != 0:
        return None

    branch = _run_git(working_tree, ["branch", "--show-current"])
    if branch is None or branch.returncode != 0:
        raise RuntimeError(f"Gitの現在branch取得に失敗: {claude_common.format_cli_error(branch)}")
    if (branch.stdout or "").strip() != "develop":
        return None

    origin_master = _run_git(working_tree, ["rev-parse", "--verify", "origin/master^{commit}"])
    if origin_master is None or origin_master.returncode != 0:
        raise RuntimeError(f"origin/masterの解決に失敗: {claude_common.format_cli_error(origin_master)}")

    tracked_diff = _run_git(working_tree, ["diff", "--quiet", "origin/master", "--", str(_STATUSLINE_DIR)])
    if tracked_diff is None or tracked_diff.returncode not in (0, 1):
        raise RuntimeError(f"statusline差分の確認に失敗: {claude_common.format_cli_error(tracked_diff)}")
    if tracked_diff.returncode == 1:
        return working_tree

    untracked = _run_git(working_tree, ["ls-files", "--others", "--exclude-standard", "--", str(_STATUSLINE_DIR)])
    if untracked is None or untracked.returncode != 0:
        raise RuntimeError(f"statuslineの未追跡ファイル確認に失敗: {claude_common.format_cli_error(untracked)}")
    return working_tree if (untracked.stdout or "").strip() else None


def _run_git(
    working_tree: pathlib.Path,
    args: list[str],
) -> subprocess.CompletedProcess[str] | None:
    """指定したGit作業ツリーでGitコマンドを実行する。"""
    return claude_common.run_subprocess(
        ["git", *args],
        timeout=_GIT_TIMEOUT,
        cwd=working_tree,
        tag="statusline",
    )


def _install_development_binary(working_tree: pathlib.Path) -> bool:
    """miseからビルドしたstatuslineを原子的に配置する。"""
    mise_bin = setup_mise.find_mise_binary()
    if mise_bin is None:
        raise RuntimeError("statusline開発版のビルドに必要なmiseが見つからない")

    build = claude_common.run_subprocess(
        [
            str(mise_bin),
            "exec",
            "--",
            "cargo",
            "build",
            "--release",
            "--locked",
            "--manifest-path",
            str(_STATUSLINE_MANIFEST),
        ],
        timeout=_BUILD_TIMEOUT,
        cwd=working_tree,
        tag="statusline",
    )
    if build is None or build.returncode != 0:
        raise RuntimeError(f"statusline開発版のビルドに失敗: {claude_common.format_cli_error(build)}")

    artifact = working_tree / _BUILD_DIR / ("claude-statusline.exe" if sys.platform == "win32" else "claude-statusline")
    content = artifact.read_bytes()
    _ETAG_PATH.unlink(missing_ok=True)
    mode = None if sys.platform == "win32" else 0o755
    if not claude_common.atomic_write_bytes(_INSTALL_PATH, content, mode=mode, tag="statusline"):
        raise RuntimeError(f"statusline開発版の配置に失敗: {_INSTALL_PATH}")
    logger.info(log_format.format_status("statusline", f"開発版をインストールしました: {_INSTALL_PATH}"))
    return True


def _download_release(client: httpx.Client | None = None) -> bool:
    """GitHub Releaseからclaude-statuslineバイナリを配置する。

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
        download_url = os.environ.get(_DOWNLOAD_URL_ENV) or _DOWNLOAD_URL
        response = active_client.get(download_url, headers=headers)
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
