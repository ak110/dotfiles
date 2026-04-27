"""Windows レジストリ設定を winreg で直接書き込むモジュール。

`chezmoi apply` 後処理 (`pytools.post_apply`) から Windows 環境でのみ呼ばれる。
書き込み対象は `_REGISTRY_SETTINGS` を SSOT とし、`HKEY_CURRENT_USER` 配下のみを扱う。
非 Windows では何もせずスキップする。
"""

import dataclasses
import logging
import os
import typing
from collections.abc import Callable, Sequence

from pytools._internal import log_format, winutils

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"


@dataclasses.dataclass(frozen=True)
class _RegistrySpec:
    """書き込み対象のレジストリ値定義。

    `value_type` は winreg モジュールの定数名（例: ``"REG_DWORD"``）を保持する。
    モジュールロードを Windows 限定にするため winreg 定数を直接参照しない。
    """

    description: str
    sub_key: str
    value_name: str
    value_type: str
    value: int | str | bytes


_REGISTRY_SETTINGS: list[_RegistrySpec] = [
    _RegistrySpec(
        description="Explorer の拡張子表示を有効化",
        sub_key=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
        value_name="HideFileExt",
        value_type="REG_DWORD",
        value=0,
    ),
    _RegistrySpec(
        description="タスクバーの検索ボックスを最小化",
        sub_key=r"Software\Microsoft\Windows\CurrentVersion\Search",
        value_name="SearchboxTaskbarMode",
        value_type="REG_DWORD",
        value=0,
    ),
    # Explorer の `link` 値は本来 4 バイトの BINARY であり、DWORD で書くと
    # 一部環境で「～へのショートカット」抑止が効かない。ここでは BINARY で正しく書き込む。
    _RegistrySpec(
        description="新規ショートカット作成時に「～へのショートカット」を付けない",
        sub_key=r"Software\Microsoft\Windows\CurrentVersion\Explorer",
        value_name="link",
        value_type="REG_BINARY",
        value=b"\x00\x00\x00\x00",
    ),
    _RegistrySpec(
        description="フォルダータイプの自動認識を無効化",
        sub_key=(
            r"Software\Classes\Local Settings\Software\Microsoft\Windows"
            r"\Shell\Bags\AllFolders\Shell"
        ),
        value_name="FolderType",
        value_type="REG_SZ",
        value="NotSpecified",
    ),
]


def run(
    *,
    is_windows: bool | None = None,
    apply_fn: Callable[[Sequence[_RegistrySpec]], None] | None = None,
) -> bool:
    """Windows でレジストリ設定を書き込む。

    Returns:
        非 Windows ではスキップして ``False``、書き込みを実行した場合 ``True``。
    """
    win = _IS_WINDOWS if is_windows is None else is_windows
    if not win:
        return False
    (apply_fn or _apply_all)(_REGISTRY_SETTINGS)
    return True


def _apply_all(specs: Sequence[_RegistrySpec]) -> None:
    """`specs` の各エントリを HKCU 配下に書き込む。"""
    wr = winutils.import_winreg()
    for spec in specs:
        logger.info(log_format.format_status("registry", spec.description))
        _write_value(wr, spec)


def _write_value(wr: typing.Any, spec: _RegistrySpec) -> None:
    r"""`spec` を HKCU 配下に書き込む。

    親キーが未作成のパス（`...\\Bags\\AllFolders\\Shell` など）を含むため、
    中間キーをまとめて作成する `CreateKeyEx` を使う（`OpenKey` では `FileNotFoundError` になる）。
    """
    reg_type = getattr(wr, spec.value_type)
    with wr.CreateKeyEx(wr.HKEY_CURRENT_USER, spec.sub_key, 0, wr.KEY_SET_VALUE) as key:
        wr.SetValueEx(key, spec.value_name, 0, reg_type, spec.value)
