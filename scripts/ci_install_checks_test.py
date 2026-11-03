"""CI用の配置と配布確認を、隔離したHOMEで実行する。"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


def test_linux_placement(tmp_path: Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """HOMEへ配置し、post工程が元のローカルactionを引き続き読み取れる。"""
    workspace = tmp_path / "workspace"
    source = workspace / "dotfiles"
    source.mkdir(parents=True)
    (source / "keep").write_text("checkout", encoding="utf-8")
    definition = source / ".github/actions/ci-test-linux/action.yml"
    definition.parent.mkdir(parents=True)
    definition.write_text("name: ci-test-linux\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    output = tmp_path / "environment"
    process = subprocess.run(
        ["bash", str(Path(__file__).with_name("ci_place_linux.sh"))],
        cwd=workspace,
        env={**host_environ(), "HOME": str(home), "GITHUB_WORKSPACE": str(workspace), "GITHUB_ENV": str(output)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    assert definition.read_text(encoding="utf-8") == "name: ci-test-linux\n"
    assert (home / "dotfiles/keep").read_text(encoding="utf-8") == "checkout"
    assert output.read_text(encoding="utf-8").strip() == f"DOTFILES_DIR={home}/dotfiles"


@pytest.mark.parametrize("missing", ["", ".vimrc", "path"])
def test_linux_distribution_check(tmp_path: Path, host_environ: Callable[[], dict[str, str]], missing: str) -> None:
    """配布ファイルとPATH設定がそろった場合だけ配布確認が成功する。"""
    for name in (".bashrc", ".vimrc", ".claude/rules/agent-toolkit/01-agent.md", ".ssh/config"):
        if name == missing:
            continue
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("" if missing == "path" else "$HOME/dotfiles/bin", encoding="utf-8")
    process = subprocess.run(
        ["bash", str(Path(__file__).with_name("ci_verify_linux.sh"))],
        env={**host_environ(), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    assert process.returncode == (1 if missing else 0), process.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
@pytest.mark.parametrize("copy_code", [1, 8])
@pytest.mark.parametrize("action_name", ["ci-test-windows", "ci-update-dotfiles-upgrade-windows"])
def test_windows_placement_copy_boundary(
    tmp_path: Path, host_environ: Callable[[], dict[str, str]], copy_code: int, action_name: str
) -> None:
    """robocopyの成功コードを正規化し、成功後もpost用のaction定義を保持する。"""
    source = tmp_path / "dotfiles"
    source.mkdir()
    definition = source / f".github/actions/{action_name}/action.yml"
    definition.parent.mkdir(parents=True)
    definition.write_text(f"name: {action_name}\n", encoding="utf-8")
    profile = tmp_path / "profile"
    profile.mkdir()
    output = tmp_path / "environment"
    script = Path(__file__).with_name("ci_place_windows.ps1").read_text(encoding="utf-8-sig")
    probe = tmp_path / "placement.ps1"
    probe.write_text(
        "function robocopy { param($source, $destination) "
        f"if ({copy_code} -lt 8) {{ Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force }}; "
        f"$global:LASTEXITCODE = {copy_code} }}\n" + script,
        encoding="utf-8-sig",
    )
    process = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(probe)],
        env={
            **host_environ(),
            "GITHUB_WORKSPACE": str(tmp_path),
            "USERPROFILE": str(profile),
            "GITHUB_ENV": str(output),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    assert process.returncode == (0 if copy_code < 8 else copy_code), process.stdout + process.stderr
    assert definition.read_text(encoding="utf-8") == f"name: {action_name}\n"
    assert output.exists() == (copy_code < 8)
    if copy_code < 8:
        installed = profile / f"dotfiles/.github/actions/{action_name}/action.yml"
        assert installed.read_bytes() == definition.read_bytes()
