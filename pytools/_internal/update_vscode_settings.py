"""VSCode の settings.json をホスト固有設定で更新するモジュール。

ホスト名から生成した Activity Bar の色と Markdown 関連の CSS 設定を、
OS に応じた scope の settings.json にマージする。

対象 scope:

- Windows (ローカル VSCode の User scope):
  ``%APPDATA%/Code/User/settings.json``
  全 VSCode インスタンスで共有されるユーザー設定。Settings Sync 対象。
- Linux (Remote SSH 時の Machine scope):
  ``~/.vscode-server/data/Machine/settings.json``
  Remote Host ごとの上書き設定。User scope より優先される。

scope ごとの管理対象:

- ``workbench.colorCustomizations`` (ホスト色): 両 scope
  ホスト名から生成した色で Activity Bar の背景を差し替え、
  接続中の VSCode ウィンドウがどのマシンのものか一目で分かるようにする。
- ``markdown-pdf.styles`` (絶対パス): 両 scope
  各マシンのホームディレクトリに依存するため scope ごとに書き分ける。
  yzane/vscode-markdown-pdf 拡張は絶対パスを正式サポートする一方、
  HTTPS URL 指定は PDF 出力で CSS が適用されない可能性が公式 README に
  示唆されているため、URL 方式には統一しない。
- ``markdown.styles`` (jsDelivr URL): User scope のみ
  全マシン共通の URL で指定するため、User scope に 1 度書けば足りる。
  Machine scope 側には書かない (User scope を Machine scope が上書きする
  関係だが、同じ値を重複して書く必要がない)。過去のバージョンが
  Machine scope に絶対パスを書き込んでいた名残が残っている場合は、
  apply 時に明示的に削除する。

Markdown CSS の指定方式に関する設計メモ:

``markdown.styles`` と ``markdown-pdf.styles`` で指定方式が非対称な点に注意する。
``markdown.styles`` は VSCode 1.23 (2018) 以降セキュリティ上の制約でホーム配下の
絶対パスを受け付けず、ワークスペース相対パスまたは HTTPS URL のみを解釈する。
従来このモジュールが書き込んでいた絶対パスは長らく無視されていた。代替として
dotfiles リポジトリの CSS を HTTPS URL で配信する方式に切り替えたが、
GitHub raw URL は CSS ファイルを ``Content-Type: text/plain`` +
``X-Content-Type-Options: nosniff`` で返すため WebView がスタイルシートとして
拒否する。そのため ``text/css`` で配信する jsDelivr CDN を経由する必要がある。

マージ方針:

- dict 値は浅いマージ (既存キーを保持しつつ managed 側で上書き)
- list・スカラー値は managed 側で上書き
- ``_LEGACY_KEYS_FOR_MACHINE_SCOPE`` は Machine scope 専用で、apply 前に
  settings.json から削除する (過去バージョンの残骸を除去する目的)。
"""

import collections.abc
import copy
import hashlib
import json
import logging
import os
import platform
import socket
from pathlib import Path

import pytilpack.jsonc

from pytools._internal import log_format
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"
_DOTFILES_DIR = Path.home() / "dotfiles"

# VSCode 標準 Markdown プレビューに渡すカスタム CSS の URL。User scope にのみ
# 書き込む (Machine scope では上書き不要)。
# ホーム配下の絶対パスは VSCode 1.23 以降無視されるため HTTPS URL 方式を採用する。
# GitHub raw (raw.githubusercontent.com) は CSS を text/plain + nosniff で返し
# WebView に stylesheet として拒否されるので、text/css で配信する jsDelivr CDN を
# 経由する必要がある。ここを raw URL に安易に戻さないこと。
_MARKDOWN_STYLE_URL = "https://cdn.jsdelivr.net/gh/ak110/dotfiles@master/share/vscode/markdown.css"

# Machine scope の settings.json に残っていたら apply 時に削除するキー。
# 過去バージョンがホーム配下の絶対パスで markdown.styles を書き込んでいたが、
# VSCode 1.23 以降は無視されるため、該当エントリを明示的に削除する。
_LEGACY_KEYS_FOR_MACHINE_SCOPE: tuple[str, ...] = ("markdown.styles",)

# run() が settings_path 引数のデフォルト (自動検出) と明示的 None (パス未検出)
# を区別するためのセンチネル。
_UNSET: object = object()


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    setup_logging()
    run()


def run(
    *,
    settings_path: Path | None | object = _UNSET,
    hostname: str | None = None,
    is_windows: bool | None = None,
) -> bool:
    """VSCode settings.json を更新する。

    Args:
        settings_path: 書き込み先。省略時は OS に応じて自動検出する。
            明示的に ``None`` を渡すとパス未検出扱いでスキップする。
        hostname: Activity Bar 色生成に使うホスト名 (テスト用)。
        is_windows: 実行環境の判定オーバーライド (テスト用)。
            Windows (User scope) なら True、Linux (Machine scope) なら False。

    Returns:
        実際にファイルを書き換えたかどうか。
    """
    win = _IS_WINDOWS if is_windows is None else is_windows
    if settings_path is _UNSET:
        settings_path = _settings_path(is_windows=win)
    if settings_path is None:
        logger.info(log_format.format_status("vscode", "VSCode未検出のためスキップ"))
        return False
    assert isinstance(settings_path, Path)
    # Windows は User scope、Linux は Machine scope という使い分けを前提に、
    # scope ごとに managed の内容と削除対象のレガシーキーを切り替える。
    managed = _build_managed_settings(hostname=hostname, is_user_scope=win)
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
    """ホスト名の SHA-256 ハッシュから CVD-safe な淡色パレットを引く。

    ``_HOST_COLORS`` の離散パレットをハッシュインデックスで参照するため、
    2 ホスト同士が肉眼で区別できない近似色に落ちることは起こらない。
    完全一致の衝突率は 1/len(パレット) で上昇するが、視覚的区別性を優先する。
    """
    hostname = hostname or socket.gethostname()
    hostname = hostname.lower().removesuffix("-container")  # 末尾-containerは無視する（独自の都合により…）
    digest = hashlib.sha256(hostname.encode()).digest()
    index = int.from_bytes(digest[:4], "big") % len(_HOST_COLORS)
    return _HOST_COLORS[index]


def _build_managed_settings(*, hostname: str | None = None, is_user_scope: bool) -> dict:
    """Managed 設定の dict を構築する。

    Args:
        hostname: Activity Bar 色生成に使うホスト名。
        is_user_scope: True なら User scope (全マシン共通)、
            False なら Machine scope (マシン固有)。markdown.styles は
            User scope でのみ管理する。
    """
    share_vscode = _DOTFILES_DIR / "share" / "vscode"
    settings: dict = {
        "workbench.colorCustomizations": {
            "activityBar.background": _hostname_color(hostname=hostname),
        },
        # yzane/vscode-markdown-pdf は絶対パスを正式サポートしているため従来どおり
        # 絶対パスで渡す。HTTPS URL 指定は PDF 出力で CSS が適用されない可能性が
        # 公式 README に示唆されており、markdown.styles 側と揃えて URL 化しては
        # ならない (揃えたくなる誘惑への注意)。マシン依存のパスなので両 scope で
        # 書き込む必要がある。
        "markdown-pdf.styles": [share_vscode.joinpath("markdown-pdf.css").as_posix()],
    }
    if is_user_scope:
        # markdown.styles は jsDelivr URL で全マシン共通。User scope 側に一度
        # 書けば Settings Sync や Machine scope への波及でカバーされる。
        # Machine scope 側には書かない (同じ値を重複して持たないため)。
        settings["markdown.styles"] = [_MARKDOWN_STYLE_URL]
    return settings


def _apply(managed: dict, settings_path: Path, *, legacy_keys: tuple[str, ...] = ()) -> bool:
    """Managed 設定を settings.json にマージして書き込む。

    dict 値は浅いマージ (既存キーを保持)、それ以外は上書き。
    VSCode の settings.json は JSONC 形式のため pytilpack.jsonc.loads でパースする。
    書き込みは標準 JSON で行う (コメントは保持しない)。

    ``legacy_keys`` はマージ前に settings.json から削除する。現在は管理しない
    キーの残骸 (過去バージョンが書き込んだ無効な絶対パス等) を除去するための
    仕組みで、Machine scope から markdown.styles を削除する用途などで使う。

    Returns:
        実際にファイルを書き換えたかどうか。
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
    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(log_format.format_status(short, "更新しました"))
    return True


if __name__ == "__main__":
    _main()
