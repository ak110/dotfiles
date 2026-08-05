"""post-applyテンプレートの最終実行順序を検証する。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
LINUX_TEMPLATE = REPO_ROOT / ".chezmoi-source/run_after_post-apply.sh.tmpl"
WINDOWS_TEMPLATE = REPO_ROOT / ".chezmoi-source/run_after_post-apply-windows.ps1.tmpl"


def _read(path: Path) -> str:
    """BOMの有無を許容してテンプレートを読む。"""
    return path.read_text(encoding="utf-8-sig")


def test_linux_post_apply_is_final_operation() -> None:
    """Linux成功分岐はpost-applyをexecし、後続出力を持たない。"""
    text = _read(LINUX_TEMPLATE)
    final_block = text[text.rindex('post_apply_bin="$HOME/.local/bin/dotfiles-post-apply"') :].strip()

    assert 'exec "$post_apply_bin"' in final_block
    assert final_block.splitlines()[-4:] == [
        '    exec "$post_apply_bin"',
        "else",
        '    echo "  [pytools] $post_apply_bin が見つからないため後処理スキップ"',
        "fi",
    ]


def test_windows_post_apply_final_condition_has_branch_local_order() -> None:
    """Windowsの復帰処理後にpost-applyの最終条件分岐だけを置く。"""
    text = _read(WINDOWS_TEMPLATE)
    final_start = text.rindex("$postApplyBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-post-apply.exe'")
    before = text[:final_start]
    final_block = text[final_start:].strip()

    assert "Start-Process" in before
    assert "Start-Process" not in final_block
    assert final_block.splitlines() == [
        "$postApplyBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-post-apply.exe'",
        "if (Test-Path $postApplyBin) {",
        "    # dotfiles-post-apply は chezmoi から独立した CLI のため、workingTree を環境変数で渡す",
        "    $env:CHEZMOI_WORKING_TREE = '{{ .chezmoi.workingTree }}'",
        "    & $postApplyBin",
        "} else {",
        '    Write-Host "  [pytools] $postApplyBin が見つからないため後処理スキップ"',
        "}",
    ]


def test_windows_media_remote_has_pre_post_apply_fallback() -> None:
    """post-apply前のmedia-remote復帰に配布バイナリの代替経路を持つ。"""
    text = _read(WINDOWS_TEMPLATE)
    final_start = text.rindex("$postApplyBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-post-apply.exe'")
    before = text[:final_start]

    assert "$mediaRemoteBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-media-remote.exe'" in before
    assert before.count("elseif (Test-Path $mediaRemoteBin)") == 2
    assert before.count("Start-Process -FilePath $mediaRemoteBin -WindowStyle Hidden -ArgumentList @('serve')") == 2
