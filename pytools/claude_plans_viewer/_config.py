"""設定ファイルパス解決とTOML読み込み。"""

import logging
import os
import pathlib
import subprocess
import tomllib
from typing import Any

import platformdirs

from pytools.claude_plans_viewer import _state

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

NEW_SOURCE_ID = "private-notes-plans"
LEGACY_SOURCE_ID = "claude-plans"
NEW_PORTABLE_ROOT = "$(atk config get private_notes)/plans"
LEGACY_PORTABLE_ROOT = "~/.claude/plans"
_UNRESOLVED_PRIVATE_NOTES_ROOT = pathlib.Path.home() / ".claude" / ".plans-viewer-private-notes-unresolved"


def _canonical(path: pathlib.Path) -> pathlib.Path:
    """rootの比較・ファイル参照に使う正規化済みパスを返す。"""
    return path.expanduser().resolve()


def explicit_root_spec(root: str | pathlib.Path) -> "_state.RootSpec":
    """既存の明示rootを単一root定義へ変換する。"""
    path = _canonical(pathlib.Path(root))
    legacy = _canonical(pathlib.Path.home() / ".claude" / "plans")
    portable = LEGACY_PORTABLE_ROOT if path == legacy else str(path).replace("\\", "/")
    return _state.RootSpec(source_id="", path=path, portable_path=portable)


def _private_notes_result() -> tuple[pathlib.Path | None, str | None]:
    """`atk config get private_notes`の結果と、失敗時のroot警告を返す。"""
    try:
        completed = subprocess.run(
            ["atk", "config", "get", "private_notes"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        warning = f"private_notesの取得に失敗しました: {error}"
        logger.warning("%s。旧rootを継続します", warning)
        return None, warning
    value = completed.stdout.strip()
    if not value or "\n" in value:
        warning = "private_notesの取得結果が不正です"
        logger.warning("%s。旧rootを継続します", warning)
        return None, warning
    return _canonical(pathlib.Path(value)), None


def _private_notes_path() -> pathlib.Path | None:
    """`atk config get private_notes`の結果をroot解決用に取得する。"""
    path, _ = _private_notes_result()
    return path


def default_root_specs() -> tuple["_state.RootSpec", ...]:
    """無指定時に使う新旧rootを解決し、重複rootを除いた定義を返す。"""
    specs: list[_state.RootSpec] = []
    private_notes, warning = _private_notes_result()
    if private_notes is not None:
        specs.append(
            _state.RootSpec(
                source_id=NEW_SOURCE_ID,
                path=private_notes / "plans",
                portable_path=NEW_PORTABLE_ROOT,
                migrate_legacy_ctime=False,
            )
        )
    else:
        specs.append(
            _state.RootSpec(
                source_id=NEW_SOURCE_ID,
                path=_UNRESOLVED_PRIVATE_NOTES_ROOT,
                portable_path=NEW_PORTABLE_ROOT,
                warning=warning or "private_notesを解決できません",
                migrate_legacy_ctime=False,
            )
        )
    specs.append(
        _state.RootSpec(
            source_id=LEGACY_SOURCE_ID,
            path=pathlib.Path.home() / ".claude" / "plans",
            portable_path=LEGACY_PORTABLE_ROOT,
            migrate_legacy_ctime=True,
        )
    )
    return _state.normalize_root_specs(specs)


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
