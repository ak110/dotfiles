"""VSCodeのsettings.jsonをホスト固有設定で更新するモジュール。

ホスト名から生成したActivity Barの色とMarkdown関連のCSS設定を、
WindowsではUser scope（`%APPDATA%/Code/User/settings.json`）、
Linux Remote SSHではMachine scope（`~/.vscode-server/data/Machine/settings.json`）へ
マージする。
"""

import collections.abc
import copy
import hashlib
import logging
import os
import platform
import socket
import sys
from pathlib import Path

import pytilpack.jsonc

from pytools._internal import claude_common, log_format
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# VSCode 標準 Markdown プレビューに渡すカスタム CSS の URL。User scope にのみ
# 書き込む (Machine scope では上書き不要)。
# ホーム配下の絶対パスは VSCode 1.23 以降無視されるため HTTPS URL 方式を採用する。
# GitHub raw (raw.githubusercontent.com) は CSS を text/plain + nosniff で返し
# WebView に stylesheet として拒否されるので、text/css で配信する jsDelivr CDN を
# 経由する必要がある。ここを raw URL に安易に戻さないこと。
_MARKDOWN_STYLE_URL = "https://cdn.jsdelivr.net/gh/ak110/dotfiles@master/share/vscode/markdown.css"

# Machine scope の settings.json に残っていたら apply 時に削除するキー。
# ホーム配下の絶対パス形式の markdown.styles は VSCode 1.23 以降で無視されるため、
# 該当エントリを明示的に削除する。
_LEGACY_KEYS_FOR_MACHINE_SCOPE: tuple[str, ...] = ("markdown.styles",)


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    setup_logging()
    run()
    sys.exit(0)


def run(
    *,
    hostname: str | None = None,
    is_windows: bool | None = None,
    home: Path | None = None,
    environ: collections.abc.Mapping[str, str] | None = None,
) -> bool:
    """VSCode settings.json を更新する。

    Args:
        hostname: Activity Bar 色生成に使うホスト名 (テスト用)。
        is_windows: 実行環境の判定オーバーライド (テスト用)。
            Windows (User scope) なら True、Linux (Machine scope) なら False。
        home: ホームディレクトリのオーバーライド (テスト用)。
        environ: 環境変数マッピングのオーバーライド (テスト用)。

    Returns:
        実際にファイルを書き換えたかどうか。
    """
    win = _IS_WINDOWS if is_windows is None else is_windows
    settings_path = _settings_path(is_windows=win, home=home, environ=environ)
    if settings_path is None:
        logger.info(log_format.format_status("vscode", "VSCode未検出のためスキップ"))
        return False
    # Windows は User scope、Linux は Machine scope という使い分けを前提に、
    # scope ごとに managed の内容と削除対象のレガシーキーを切り替える。
    managed = _build_managed_settings(hostname=hostname, is_user_scope=win, home=home)
    legacy_keys: tuple[str, ...] = () if win else _LEGACY_KEYS_FOR_MACHINE_SCOPE
    return _apply(managed, settings_path, legacy_keys=legacy_keys)


def _settings_path(
    *,
    is_windows: bool | None = None,
    home: Path | None = None,
    environ: collections.abc.Mapping[str, str] | None = None,
) -> Path | None:
    """OS に応じた VSCode settings.json のパスを返す。

    VSCode のベースディレクトリが存在しない場合は None を返す。
    """
    win = _IS_WINDOWS if is_windows is None else is_windows
    env = os.environ if environ is None else environ
    if win:
        appdata = env.get("APPDATA")
        if not appdata:
            return None
        base = Path(appdata) / "Code"
    else:
        base = (home or Path.home()) / ".vscode-server"
    if not base.exists():
        return None
    if win:
        return base / "User" / "settings.json"
    return base / "data" / "Machine" / "settings.json"


# Activity Bar 背景用の CVD-safe 淡色パレット。
# Paul Tol の qualitative scheme "light" (grey 除く 8 色) に "pale" の
# 4 色 (grey と pale red を除く) を合成した 12 色構成で、deutan/protan 下でも
# 相互に区別できる。pale red は pink (#FFAABB) と RGB 距離 38 と近接するため
# 除外している。全ペアの RGB 距離の最小値は 41.64 (light cyan vs pale blue)
# あり、連続 HSL サンプリング時代にユーザーが遭遇した近接ペア
# (#afd19e vs #c6cba1、距離 23.96) を 74% 上回る。
# 参照: https://personal.sron.nl/~pault/#sec:qualitative
_HOST_COLORS: tuple[str, ...] = (
    # Tol light (grey 除く 8 色)
    "#77AADD",
    "#99DDFF",
    "#44BB99",
    "#BBCC33",
    "#AAAA00",
    "#EEDD88",
    "#EE8866",
    "#FFAABB",
    # Tol pale (grey と pale red 除く 4 色)
    "#BBCCEE",
    "#CCEEFF",
    "#CCDDAA",
    "#EEEEBB",
)


def _hostname_color(*, hostname: str | None = None) -> str:
    """ホスト名のSHA-256ハッシュからCVD-safeな淡色パレットを参照する。

    ``_HOST_COLORS`` の離散パレットをハッシュインデックスで参照するため、
    2ホスト同士が肉眼で区別できない近似色になることはない。
    完全一致の衝突率は `1/len(パレット)` で上昇するが、視覚的区別性を優先する。
    """
    hostname = hostname or socket.gethostname()
    hostname = hostname.lower().removesuffix("-container")  # コンテナ環境でも物理ホスト名で色を統一する
    digest = hashlib.sha256(hostname.encode()).digest()
    index = int.from_bytes(digest[:4], "big") % len(_HOST_COLORS)
    return _HOST_COLORS[index]


def _build_managed_settings(*, hostname: str | None = None, is_user_scope: bool, home: Path | None = None) -> dict:
    """managed設定のdictを構築する。

    Args:
        hostname: Activity Bar色生成に使うホスト名。
        is_user_scope: TrueならUser scope（全マシン共通）、
            FalseならMachine scope（マシン固有）。`markdown.styles` は
            User scopeでのみ管理する。
        home: ホームディレクトリのオーバーライド (テスト用)。
    """
    dotfiles_dir = (home or Path.home()) / "dotfiles"
    share_vscode = dotfiles_dir / "share" / "vscode"
    settings: dict = {
        "workbench.colorCustomizations": {
            "activityBar.background": _hostname_color(hostname=hostname),
        },
        # yzane/vscode-markdown-pdf は絶対パスを正式サポートしているため絶対パスで渡す。
        # HTTPS URL 指定は PDF 出力で CSS が適用されない可能性が公式 README に示唆されており、
        # markdown.styles 側と揃えて URL 化してはならない（揃えたくなる誘惑への注意）。
        # マシン依存のパスなので両 scope で書き込む必要がある。
        "markdown-pdf.styles": [share_vscode.joinpath("markdown-pdf.css").as_posix()],
    }
    if is_user_scope:
        # markdown.styles は jsDelivr URL で全マシン共通。User scope 側に一度
        # 書けば Settings Sync や Machine scope への波及でカバーされる。
        # Machine scope 側には書かない (同じ値を重複して持たないため)。
        settings["markdown.styles"] = [_MARKDOWN_STYLE_URL]
    return settings


def _apply(managed: dict, settings_path: Path, *, legacy_keys: tuple[str, ...] = ()) -> bool:
    """managed設定を`settings.json`にマージして書き込む。

    dict値は浅いマージ（既存キーを保持）、それ以外は上書き。
    VSCodeの`settings.json`はJSONC形式のため`pytilpack.jsonc.loads`でパースする。
    書き込みは`claude_common.write_settings_hybrid`へ委譲する。既存パスの値置換のみ
    で済む場合はJSONCコメント・空行・インデントを維持したまま更新し、構造変化
    （キー追加・list変更）を含む場合は全書き換えするためコメントは保持しない。

    ``legacy_keys`` はマージ前に`settings.json`から削除する。管理しなくなった
    キーの残骸（過去バージョンが書き込んだ無効な絶対パス等）を除去するための
    仕組みで、Machine scopeから`markdown.styles`を削除する用途などで使う。

    Returns:
        実際にファイルを書き換えた場合True。
    """
    data = pytilpack.jsonc.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    original = copy.deepcopy(data)

    for key in legacy_keys:
        data.pop(key, None)

    for key, value in managed.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value

    short = log_format.home_short(settings_path)
    if data == original:
        logger.info(log_format.format_status(short, "変更なし"))
        return False
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if not claude_common.write_settings_hybrid(settings_path, original, data, tag=short):
        return False
    logger.info(log_format.format_status(short, "更新しました"))
    return True


if __name__ == "__main__":
    main()
