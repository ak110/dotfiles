"""`~/.tmux/plugins/`配下のtmuxプラグインを冪等に導入・更新する。

対象プラグインの一覧と固定参照（ブランチ・タグ）は`_PLUGINS`で定義する。
Linuxのみ対象とする（Windowsはtmux利用想定外のためスキップする）。
既存ディレクトリは`.git`存在と`origin` URLの一致を確認してから更新コマンドを発行し、
利用者管理ディレクトリの誤上書きを防ぐ。
"""

import logging
import os
import platform
from dataclasses import dataclass
from pathlib import Path

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

_TMUX_PLUGINS_DIR = Path.home() / ".tmux" / "plugins"
_TAG = "tmux plugins"
_GIT_ORIGIN_BASE_ENV = "DOTFILES_TMUX_PLUGIN_ORIGIN_BASE"
_GITHUB_PREFIX = "https://github.com/"


def _effective_origin(plugin: "_Plugin") -> str:
    """テスト時に`origin`をローカルミラーへ差し替えるためのフック。

    環境変数`DOTFILES_TMUX_PLUGIN_ORIGIN_BASE`が設定されている場合、
    `https://github.com/`プレフィックスを当該値へ置換した値を返す。
    未設定時は`plugin.origin`をそのまま返す（本番動作）。
    """
    base_override = os.environ.get(_GIT_ORIGIN_BASE_ENV)
    if not base_override:
        return plugin.origin
    suffix = plugin.origin.removeprefix(_GITHUB_PREFIX)
    return f"{base_override.rstrip('/')}/{suffix}"


@dataclass(frozen=True)
class _Plugin:
    dest: Path
    origin: str
    pin: str
    pin_is_tag: bool


_PLUGINS: tuple[_Plugin, ...] = (
    _Plugin(
        dest=_TMUX_PLUGINS_DIR / "tpm",
        origin="https://github.com/tmux-plugins/tpm.git",
        pin="master",
        pin_is_tag=False,
    ),
    _Plugin(
        dest=_TMUX_PLUGINS_DIR / "tmux",
        origin="https://github.com/catppuccin/tmux.git",
        pin="v2.3.0",
        pin_is_tag=True,
    ),
    _Plugin(
        dest=_TMUX_PLUGINS_DIR / "tmux-cpu",
        origin="https://github.com/tmux-plugins/tmux-cpu.git",
        pin="master",
        pin_is_tag=False,
    ),
)


def run() -> bool:
    """tmuxプラグインを冪等に導入・更新する。"""
    if platform.system() != "Linux":
        logger.info(log_format.format_status(_TAG, "Linux以外のためスキップ"))
        return False
    changed = False
    for plugin in _PLUGINS:
        if _process_plugin(plugin):
            changed = True
    return changed


def _process_plugin(plugin: _Plugin) -> bool:
    if not plugin.dest.exists():
        return _clone(plugin)
    if not (plugin.dest / ".git").exists():
        logger.warning(
            log_format.format_status(
                _TAG,
                f"`.git`が無いためスキップ: {plugin.dest}",
            )
        )
        return False
    if not _origin_matches(plugin):
        logger.warning(
            log_format.format_status(
                _TAG,
                f"想定外の`origin`のためスキップ: {plugin.dest}",
            )
        )
        return False
    return _update(plugin)


def _clone(plugin: _Plugin) -> bool:
    plugin.dest.parent.mkdir(parents=True, exist_ok=True)
    result = claude_common.run_subprocess(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            plugin.pin,
            _effective_origin(plugin),
            str(plugin.dest),
        ],
        tag=_TAG,
    )
    if result is None or result.returncode != 0:
        logger.warning(log_format.format_status(_TAG, f"clone失敗: {plugin.dest}"))
        return False
    logger.info(log_format.format_status(_TAG, f"clone: {plugin.dest} ({plugin.pin})"))
    return True


def _origin_matches(plugin: _Plugin) -> bool:
    result = claude_common.run_subprocess(
        ["git", "-C", str(plugin.dest), "remote", "get-url", "origin"],
        tag=_TAG,
    )
    if result is None or result.returncode != 0:
        return False
    return result.stdout.strip() == _effective_origin(plugin)


def _update(plugin: _Plugin) -> bool:
    if plugin.pin_is_tag:
        return _update_to_tag(plugin)
    return _update_to_branch(plugin)


def _update_to_tag(plugin: _Plugin) -> bool:
    fetch = claude_common.run_subprocess(
        ["git", "-C", str(plugin.dest), "fetch", "--depth", "1", "origin", plugin.pin],
        tag=_TAG,
    )
    if fetch is None or fetch.returncode != 0:
        logger.warning(log_format.format_status(_TAG, f"fetch失敗: {plugin.dest}"))
        return False
    checkout = claude_common.run_subprocess(
        ["git", "-C", str(plugin.dest), "checkout", "FETCH_HEAD"],
        tag=_TAG,
    )
    if checkout is None or checkout.returncode != 0:
        logger.warning(log_format.format_status(_TAG, f"checkout失敗: {plugin.dest}"))
        return False
    logger.info(log_format.format_status(_TAG, f"更新: {plugin.dest} ({plugin.pin})"))
    return True


def _update_to_branch(plugin: _Plugin) -> bool:
    result = claude_common.run_subprocess(
        ["git", "-C", str(plugin.dest), "pull", "--ff-only"],
        tag=_TAG,
    )
    if result is None or result.returncode != 0:
        logger.warning(log_format.format_status(_TAG, f"pull失敗: {plugin.dest}"))
        return False
    logger.info(log_format.format_status(_TAG, f"更新: {plugin.dest} ({plugin.pin})"))
    return True
