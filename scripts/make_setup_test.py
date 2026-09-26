"""Makefileの初期導入targetを無害なスタブで検証する。"""

import os
import pathlib
import shutil
import subprocess

import pytest

_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
_STUB_COMMANDS = ("uv", "sudo", "apt-get", "dpkg", "wget", "pwsh", "rm")


def _make_stubbed_setup(
    tmp_path: pathlib.Path,
    target: str,
    os_release: tuple[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """複製したMakefileを隔離PATHで起動し、危険な実コマンドへの到達を防ぐ。"""
    make_path = shutil.which("make")
    assert make_path is not None
    makefile = tmp_path / "Makefile"
    makefile_content = (_REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    if os_release is not None:
        distribution, version = os_release
        (tmp_path / "os-release").write_text(f"ID={distribution}\nVERSION_ID={version}\n", encoding="utf-8")
        assert makefile_content.count(". /etc/os-release") == 1
        makefile_content = makefile_content.replace(". /etc/os-release", ". ./os-release", 1)
    makefile.write_text(makefile_content, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "called.txt"
    stub = (
        "#!/bin/sh\n"
        'printf \'%s %s\\n\' "${0##*/}" "$*" >> "$STUB_LOG"\n'
        'if [ "${0##*/}" = wget ]; then : > packages-microsoft-prod.deb; fi\n'
    )
    for name in _STUB_COMMANDS:
        path = bin_dir / name
        path.write_text(stub, encoding="utf-8")
        path.chmod(0o755)
    environment = os.environ.copy()
    environment.update({"PATH": str(bin_dir), "STUB_LOG": str(log_path)})
    result = subprocess.run(
        [make_path, "-f", str(makefile), target],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    return result, calls


@pytest.mark.skipif(shutil.which("make") is None, reason="make未インストール")
@pytest.mark.parametrize(("distribution", "version"), (("ubuntu", "24.04"), ("debian", "12"), ("debian", "13")))
def test_setup_pwsh_selects_distribution_repository(tmp_path: pathlib.Path, distribution: str, version: str) -> None:
    """OSごとのMicrosoftリポジトリ設定を、ホストを変更せずに選ぶ。"""
    result, calls = _make_stubbed_setup(tmp_path, "setup-pwsh", os_release=(distribution, version))
    assert result.returncode == 0, result.stderr
    assert f"wget --quiet https://packages.microsoft.com/config/{distribution}/{version}/packages-microsoft-prod.deb" in calls
    # Debian 13のリポジトリに存在しないパッケージと移行用のダミーパッケージを導入対象へ含めない
    install_calls = [call for call in calls if "apt-get install" in call]
    assert install_calls
    for call in install_calls:
        packages = call.split()
        assert "software-properties-common" not in packages
        assert "apt-transport-https" not in packages
