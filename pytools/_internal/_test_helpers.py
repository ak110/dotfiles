"""テスト用の共通ヘルパー。"""

import json
import pathlib

from pytools._internal import claude_common as _claude_common


class _FakeResult:
    """subprocess.CompletedProcess の軽量な代替。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _plugin_list_json(*entries: dict[str, object]) -> str:
    """テスト用の `claude plugin list --json` 出力を組み立てる。"""
    return json.dumps(list(entries), ensure_ascii=False)


def write_known_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """known_marketplaces.json に対象 marketplace のエントリを保存する。"""
    path.write_text(
        json.dumps({_claude_common.MARKETPLACE_NAME: entry}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_settings_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """settings.json.extraKnownMarketplaces に対象 marketplace のエントリを保存する。"""
    path.write_text(
        json.dumps({"extraKnownMarketplaces": {_claude_common.MARKETPLACE_NAME: entry}}, ensure_ascii=False),
        encoding="utf-8",
    )
