"""実際のrules一覧と両プラットフォームの配布一覧の整合を検証する。"""

import importlib

cases = importlib.import_module("gen-install-files_test")


def test_current_repo_files_are_synced() -> None:
    # 同じ期待値を組み立てるテスト専用ヘルパーを共有する。
    names = sorted(path.name for path in (cases._REPO_ROOT / "agent-toolkit" / "rules").glob("*.md"))  # pylint: disable=protected-access
    sh_block = cases._extract_block((cases._REPO_ROOT / "install-claude.sh").read_text(encoding="utf-8"))  # pylint: disable=protected-access
    ps1_block = cases._extract_block((cases._REPO_ROOT / "install-claude.ps1").read_text(encoding="utf-8-sig"))  # pylint: disable=protected-access

    assert sh_block == cases._expected_sh_block(names)  # pylint: disable=protected-access
    assert ps1_block == cases._expected_ps1_block(names)  # pylint: disable=protected-access
