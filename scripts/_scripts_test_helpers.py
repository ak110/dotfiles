"""`scripts/`配下のテストが共有する補助関数と定数。

pytestのprependモードではリポジトリ直下と`scripts/`がともに`sys.path`へ入り、同名のトップレベルモジュールは
先に読み込んだ方だけが`sys.modules`に残る。直下の`_test_helpers.py`と衝突しないよう、本ファイルは別の名前にする。
"""

import pathlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TWO_LAYER_WAIT_HEADING = "## agents_serverの二層待機"


def markdown_section(text: str, heading: str) -> str:
    match = re.search(rf"^{re.escape(heading)}\n(?P<body>.*?)(?=^#{{1,3}} |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match is not None
    return match.group("body")


def _write(tmp_path: pathlib.Path, body: str, *, name: str = "doc.md") -> pathlib.Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


_DENSITY_BODY = (
    "---\n"
    "title: 例\n"
    "---\n"
    "<!-- 注記の1行目\n"
    "注記の2行目\n"
    "-->\n"
    "前置きの文である。対象は変更しない。条件は変えない。順序は変えない。\n"
    "- 結合する項目は変更し\n"
    "ない。\n"
    "値を読む。値は書かない。出力は削らない。\n"
    "当該の値を記録する。当該の行を読む。\n" + "観測した値を記録する。\n" * 13
)
