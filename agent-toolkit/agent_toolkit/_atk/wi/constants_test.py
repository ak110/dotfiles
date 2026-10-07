"""WIの状態と種別の定数の定義箇所のテスト。"""

import pathlib
import re


def test_state_sets_have_single_definition_site() -> None:
    """状態名の値を書くモジュールを1つに保つ。

    WIの共通処理のモジュールと`uwi_scan`は依存関係を持つため、どちらかへ状態集合を置くと循環importになる。
    値の記述を`constants`だけに残し、他のモジュールは`constants`からimportする。
    旧ディレクトリ構成の読み取り互換を担う`legacy`は、廃止した状態名を含む別の集合を保持するため対象から除く。
    """
    scripts_dir = pathlib.Path(__file__).resolve().parent
    pattern = re.compile(r"""^[A-Z][A-Z0-9_]*\s*=\s*\(?\s*["'](?:inbox|processing|hold|adopted|rejected)["']""")
    definition_sites = {
        path.name
        for path in sorted(scripts_dir.glob("*.py"))
        if not path.name.endswith("_test.py")
        and path.name != "legacy.py"
        and any(pattern.search(line) for line in path.read_text(encoding="utf-8").splitlines())
    }
    assert definition_sites == {"constants.py"}
