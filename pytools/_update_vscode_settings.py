"""VSCode の settings.json をホスト固有設定で更新するモジュール。

ホスト名から生成した Activity Bar の色と Markdown CSS パスを
settings.json にマージする。

対象:
- Linux: ~/.vscode-server/data/Machine/settings.json
- Windows: %APPDATA%/Code/User/settings.json

マージ方針:
- dict 値は浅いマージ (既存キーを保持しつつ managed 側で上書き)
- list・スカラー値は managed 側で上書き
"""

import colorsys
import copy
import hashlib
import json
import logging
import os
import platform
import re
import socket
from pathlib import Path

from pytools import _log_format

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"
_DOTFILES_DIR = Path.home() / "dotfiles"


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
    run()


def run() -> bool:
    """VSCode settings.json を更新する。

    Returns:
        実際にファイルを書き換えたかどうか。
    """
    settings_path = _settings_path()
    if settings_path is None:
        logger.info(_log_format.format_status("vscode", "VSCode未検出のためスキップ"))
        return False
    managed = _build_managed_settings()
    return _apply(managed, settings_path)


def _settings_path() -> Path | None:
    """OS に応じた VSCode settings.json のパスを返す。

    VSCode のベースディレクトリが存在しない場合は None を返す。
    """
    if _IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        base = Path(appdata) / "Code"
    else:
        base = Path.home() / ".vscode-server"
    if not base.exists():
        return None
    if _IS_WINDOWS:
        return base / "User" / "settings.json"
    return base / "data" / "Machine" / "settings.json"


def _build_managed_settings() -> dict:
    """Managed 設定の dict を構築する。"""
    share_vscode = _DOTFILES_DIR / "share" / "vscode"
    return {
        "workbench.colorCustomizations": {
            "activityBar.background": _hostname_color(),
        },
        "markdown.styles": [share_vscode.joinpath("markdown.css").as_posix()],
        "markdown-pdf.styles": [share_vscode.joinpath("markdown-pdf.css").as_posix()],
    }


def _load_jsonc(text: str) -> dict:
    """JSONC (JSON with Comments) をパースする。

    VSCode の settings.json は行コメント (//)・ブロックコメント
    (/* */)・トレーリングカンマを許容する JSONC 形式である。
    コメントとトレーリングカンマを除去してから標準 json でパースする。
    """
    # コメントを除去する。文字列リテラル内の // や /* は除去しない。
    result: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        if in_string:
            if text[i] == "\\" and i + 1 < len(text):
                result.append(text[i : i + 2])
                i += 2
                continue
            if text[i] == '"':
                in_string = False
            result.append(text[i])
            i += 1
        elif text[i] == '"':
            in_string = True
            result.append(text[i])
            i += 1
        elif text[i : i + 2] == "//":
            # 行コメント: 行末まで読み飛ばす
            while i < len(text) and text[i] != "\n":
                i += 1
        elif text[i : i + 2] == "/*":
            # ブロックコメント: */ まで読み飛ばす
            i += 2
            while i < len(text) - 1 and text[i : i + 2] != "*/":
                i += 1
            i += 2
        else:
            result.append(text[i])
            i += 1

    cleaned = "".join(result)
    # トレーリングカンマを除去 (} や ] の直前)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return json.loads(cleaned)


def _apply(managed: dict, settings_path: Path) -> bool:
    """Managed 設定を settings.json にマージして書き込む。

    dict 値は浅いマージ (既存キーを保持)、それ以外は上書き。
    VSCode の settings.json は JSONC 形式のため _load_jsonc でパースする。
    書き込みは標準 JSON で行う (コメントは保持しない)。

    Returns:
        実際にファイルを書き換えたかどうか。
    """
    data = _load_jsonc(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    original = copy.deepcopy(data)

    for key, value in managed.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value

    short = _log_format.home_short(settings_path)
    if data == original:
        logger.info(_log_format.format_status(short, "変更なし"))
        return False
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(_log_format.format_status(short, "更新しました"))
    return True


def _hostname_color() -> str:
    """ホスト名の SHA-256 ハッシュから穏やかな HEX カラーを生成する。

    HSL 色空間で色相をフルレンジ、彩度を控えめ、明度を高めに設定し、
    #999999～#eeeeee 相当の明るく穏やかな色を返す。
    SHA-256 の雪崩効果により、ホスト名が 1 文字違うだけで全成分が変わる。
    """
    hostname = socket.gethostname()
    digest = hashlib.sha256(hostname.encode()).digest()
    # 異なるバイト位置から各成分を取得
    h = int.from_bytes(digest[0:2], "big") / 65535.0  # 色相: 0.0-1.0
    s = 0.25 + (int.from_bytes(digest[2:4], "big") / 65535.0) * 0.30  # 彩度: 0.25-0.55
    l_val = 0.70 + (int.from_bytes(digest[4:6], "big") / 65535.0) * 0.18  # 明度: 0.70-0.88
    # colorsys.hls_to_rgb は HLS 順 (色相, 明度, 彩度)
    r, g, b = colorsys.hls_to_rgb(h, l_val, s)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


if __name__ == "__main__":
    _main()
