"""winget configure で Windows レジストリ設定を宣言的に適用するモジュール。

`chezmoi apply` 後処理 (`pytools.post_apply`) から Windows 環境でのみ呼ばれる。
DSC リソース定義は dotfiles ルート直下の `configuration.dsc.yaml` を SSOT とする。
winget CLI / configure サブコマンド対応バージョン / DSC ファイルが揃わない場合は
スキップし、他ステップ同様 subprocess の失敗は内部で吸収する。
"""

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from pytools._internal import log_format

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"

# winget configure が正式サポートされた最小バージョン。
# v1.6.2631 より前では `configure` サブコマンドが未提供または実験的で、
# `--accept-configuration-agreements` も受け付けない。
_MIN_WINGET_VERSION = (1, 6, 2631)

# winget の初回 configure は PSDscResources 等のモジュール取得で数分かかりうる。
_WINGET_TIMEOUT = 900


def run(
    *,
    is_windows: bool | None = None,
    find_winget_fn: Callable[[], str | None] | None = None,
    get_version_fn: Callable[[str], tuple[int, ...] | None] | None = None,
    apply_fn: Callable[[str, Path], bool] | None = None,
) -> bool:
    """Windows 環境で winget configure を実行する。

    Returns:
        configure が成功した場合 True。スキップ・失敗時は False。
    """
    win = _IS_WINDOWS if is_windows is None else is_windows
    if not win:
        return False

    winget = (find_winget_fn or _find_winget)()
    if winget is None:
        logger.info(log_format.format_status("winget", "未検出のためスキップ"))
        return False

    version = (get_version_fn or _get_winget_version)(winget)
    if version is None:
        logger.info(log_format.format_status("winget", "バージョン取得に失敗したためスキップ"))
        return False
    if version < _MIN_WINGET_VERSION:
        version_str = ".".join(str(p) for p in version)
        min_str = ".".join(str(p) for p in _MIN_WINGET_VERSION)
        logger.info(log_format.format_status("winget", f"v{version_str} は v{min_str} 未満のためスキップ"))
        return False

    working_tree = os.environ.get("CHEZMOI_WORKING_TREE")
    if not working_tree:
        logger.info(log_format.format_status("winget", "CHEZMOI_WORKING_TREE 未設定のためスキップ"))
        return False

    dsc_file = Path(working_tree) / "configuration.dsc.yaml"
    if not dsc_file.is_file():
        logger.info(log_format.format_status("winget", f"{dsc_file} が無いためスキップ"))
        return False

    return (apply_fn or _apply)(winget, dsc_file)


def _find_winget() -> str | None:
    """Winget CLI の実体パスを返す。未検出なら None。"""
    return shutil.which("winget")


def _get_winget_version(winget: str) -> tuple[int, ...] | None:
    """`winget --version` の出力から `(major, minor, patch)` を取り出す。

    winget の出力は `v1.6.2631` 形式。解析できなければ None を返してスキップさせる。
    環境によっては stdout でなく stderr にバージョンを出す実装もあるため両方を検査対象にする。
    """
    try:
        result = subprocess.run(
            [winget, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(log_format.format_status("winget", f"`--version` 実行に失敗: {e}"))
        return None
    if result.returncode != 0:
        logger.info(log_format.format_status("winget", f"`--version` が非ゼロ終了: {result.stderr.strip()}"))
        return None

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", f"{result.stdout}\n{result.stderr}")
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _apply(winget: str, dsc_file: Path) -> bool:
    """Winget configure を実行する。冪等性は DSC 側に委ねる。

    初回は DSC リソースモジュールの取得で数分かかるため、winget の出力を
    キャプチャせずそのままパススルーし、無音時間を避ける（タイムアウトは `_WINGET_TIMEOUT`）。
    """
    logger.info(log_format.format_status("winget", f"{dsc_file} を適用します（初回は数分かかることがあります）"))
    try:
        result = subprocess.run(
            [
                winget,
                "configure",
                "--accept-configuration-agreements",
                "--disable-interactivity",
                "-f",
                str(dsc_file),
            ],
            check=False,
            timeout=_WINGET_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(log_format.format_status("winget", f"`configure` 実行に失敗: {e}"))
        return False

    if result.returncode != 0:
        logger.warning(log_format.format_status("winget", f"`configure` が失敗 (rc={result.returncode})"))
        return False

    logger.info(log_format.format_status("winget", f"{dsc_file} を適用しました"))
    return True
