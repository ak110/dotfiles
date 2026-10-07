"""実際のrules一覧と両プラットフォームの配布一覧の整合を検証する。"""

from _scripts_test_helpers import REPO_ROOT, expected_ps1_block, expected_sh_block, extract_install_files_block


def test_current_repo_files_are_synced() -> None:
    names = sorted(path.name for path in (REPO_ROOT / "agent-toolkit" / "rules").glob("*.md"))
    sh_block = extract_install_files_block((REPO_ROOT / "install-claude.sh").read_text(encoding="utf-8"))
    ps1_block = extract_install_files_block((REPO_ROOT / "install-claude.ps1").read_text(encoding="utf-8-sig"))

    assert sh_block == expected_sh_block(names)
    assert ps1_block == expected_ps1_block(names)
