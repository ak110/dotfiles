"""設定ファイルパス解決とTOML読み込み。"""

import logging
import os
import pathlib
import tomllib
from typing import Any

import platformdirs

logger = logging.getLogger(__name__)

ENV_CONFIG = "CLAUDE_PLANS_VIEWER_CONFIG"

# kebab-case表記でTOMLに書かれるキーと、CLI側`argparse.Namespace`で扱う
# snake_case属性名の対応表。本モジュールはこの対応で正規化した辞書を返す。
_KEY_MAP: dict[str, str] = {
    "root": "root",
    "host": "host",
    "port": "port",
    "remote-hosts": "remote_hosts",
}


def default_config_path() -> pathlib.Path:
    r"""設定ファイルの既定パスを返す。

    環境変数`CLAUDE_PLANS_VIEWER_CONFIG`が設定されていればそれを優先する
    （テスト容易性確保とユーザーの強制上書き用）。
    未設定なら`platformdirs.user_config_dir("pytools", appauthor=False)`配下の
    `claude-plans-viewer.toml`を返す。Linuxでは
    `~/.config/pytools/claude-plans-viewer.toml`、
    Windowsでは`%LOCALAPPDATA%\pytools\claude-plans-viewer.toml`になる。

    `appauthor=False`を渡すのは、未指定時にWindowsで`appname`が
    appauthorとしても付与され`%LOCALAPPDATA%\pytools\pytools\...`に
    なる挙動を回避するため。
    """
    override = os.environ.get(ENV_CONFIG)
    if override:
        return pathlib.Path(override)
    return pathlib.Path(platformdirs.user_config_dir("pytools", appauthor=False)) / "claude-plans-viewer.toml"


def load_config(path: pathlib.Path | None = None) -> dict[str, Any]:
    """TOML設定ファイルを読み込み、CLI側で扱える正規化済み辞書を返す。

    ファイル不在時は空辞書を返す。TOML構文エラー時は`ValueError`を送出する。
    既知キー（`root`/`host`/`port`/`remote-hosts`）以外は警告ログを記録して無視する。
    戻り値のキーはCLI側の`argparse.Namespace`属性名と整合するsnake_case
    （例: `remote-hosts`は`remote_hosts`へ正規化）で返す。

    Args:
        path: 読み込み対象パス。`None`なら`default_config_path()`を使う。

    Raises:
        ValueError: TOML構文エラー時に送出する。
    """
    if path is None:
        path = default_config_path()
    if not path.is_file():
        return {}
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"設定ファイルのTOMLが不正です: {path}: {e}") from e

    result: dict[str, Any] = {}
    for key, value in raw.items():
        normalized = _KEY_MAP.get(key)
        if normalized is None:
            logger.warning("設定ファイルの未知キーを無視します: %s (%s)", key, path)
            continue
        # `remote-hosts`はTOMLの配列を期待する。文字列等の誤指定は警告して除外し、
        # 設定ミスを表面化させる（他キーの型違反は呼び出し側の`int()`等で
        # `ValueError`に乗るためここでは扱わない）。
        if normalized == "remote_hosts" and not isinstance(value, list):
            logger.warning(
                "設定ファイルの%sはリストを指定してください: %r (%s)",
                key,
                value,
                path,
            )
            continue
        result[normalized] = value
    return result
