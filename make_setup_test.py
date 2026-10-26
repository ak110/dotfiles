"""Makefileの初期導入targetと検査targetを無害なスタブで検証する。"""

import os
import pathlib
import shutil
import subprocess
import venv
from collections.abc import Callable

import pytest

_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent
_STUB_COMMANDS = ("uv", "sudo", "apt-get", "dpkg", "wget", "pwsh", "rm")
# 検査targetのレシピが使う実コマンド。隔離PATHへ実体へのリンクを置く。
_LINKED_COMMANDS = ("env", "cat")


def _venv_executable(environment: pathlib.Path, name: str) -> pathlib.Path:
    """OSごとのvenv実行ファイルの配置を隠す。"""
    return environment / ("Scripts" if os.name == "nt" else "bin") / f"{name}{'.exe' if os.name == 'nt' else ''}"


def _make_stubbed_setup(
    tmp_path: pathlib.Path,
    target: str,
    os_release: tuple[str, str] | None = None,
    environment_overrides: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """複製したMakefileを隔離PATHで起動し、危険な実コマンドへの到達を防ぐ。

    スタブは呼び出し名と引数を記録し、`uv`だけは起動時に受け取った`UV_PYTHON`と
    `UV_PROJECT_ENVIRONMENT`の値（未設定なら`<unset>`）も記録する。
    """
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
        'if [ "${0##*/}" = uv ]; then\n'
        '  printf \'%s %s UV_PYTHON=%s UV_PROJECT_ENVIRONMENT=%s\\n\' "${0##*/}" "$*" '
        '"${UV_PYTHON-<unset>}" "${UV_PROJECT_ENVIRONMENT-<unset>}" >> "$STUB_LOG"\n'
        "else\n"
        '  printf \'%s %s\\n\' "${0##*/}" "$*" >> "$STUB_LOG"\n'
        "fi\n"
        'if [ "${0##*/}" = wget ]; then : > packages-microsoft-prod.deb; fi\n'
    )
    for name in _STUB_COMMANDS:
        path = bin_dir / name
        path.write_text(stub, encoding="utf-8")
        path.chmod(0o755)
    for name in _LINKED_COMMANDS:
        real = shutil.which(name)
        assert real is not None
        (bin_dir / name).symlink_to(real)
    (tmp_path / ".python-version").write_text("3.15\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.update({"PATH": str(bin_dir), "STUB_LOG": str(log_path), **(environment_overrides or {})})
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


@pytest.mark.skipif(shutil.which("make") is None, reason="make未インストール")
@pytest.mark.parametrize(
    ("target", "pyfltr_arguments"), (("format", "run --frozen pyfltr fast"), ("test", "run --frozen pyfltr run --no-fix"))
)
def test_check_entry_does_not_pass_parent_uv_python(tmp_path: pathlib.Path, target: str, pyfltr_arguments: str) -> None:
    """基準外の`UV_PYTHON`を継承しても、同期後のpyfltr起動へその指定を渡さない。

    同期は`--python`で基準版を選ぶが、後続の`uv run`は継承した`UV_PYTHON`で仮想環境を再作成するため、
    検査targetがこの変数を外さないと検査が基準外の版で動く。
    """
    result, calls = _make_stubbed_setup(tmp_path, target, environment_overrides={"UV_PYTHON": "3.13"})
    assert result.returncode == 0, result.stderr
    # 同期は親の指定を受け取ったまま`--python`で基準版を選ぶ
    assert (
        f"uv sync --all-groups --all-extras --python 3.15 UV_PYTHON=3.13 UV_PROJECT_ENVIRONMENT={tmp_path / '.venv'}" in calls
    )
    assert f"uv {pyfltr_arguments} UV_PYTHON=<unset> UV_PROJECT_ENVIRONMENT={tmp_path / '.venv'}" in calls


@pytest.mark.skipif(shutil.which("make") is None, reason="make未インストール")
@pytest.mark.parametrize("target", ("sync", "format", "test", "test-browser", "test-agents-live"))
def test_check_entries_override_parent_project_environment(tmp_path: pathlib.Path, target: str) -> None:
    """検査入口は継承した外部venvでなく、各作業ツリーのvenvをuvへ渡す。"""
    result, calls = _make_stubbed_setup(
        tmp_path, target, environment_overrides={"UV_PROJECT_ENVIRONMENT": str(tmp_path / "parent-venv")}
    )
    assert result.returncode == 0, result.stderr
    expected_root = f"UV_PROJECT_ENVIRONMENT={tmp_path / '.venv'}"
    expected_toolkit = f"UV_PROJECT_ENVIRONMENT={tmp_path / 'agent-toolkit' / '.venv'}"
    assert calls
    for call in calls:
        assert expected_toolkit in call if "--project agent-toolkit" in call else expected_root in call


def test_precommit_pyfltr_entry_overrides_parent_project_environment(tmp_path: pathlib.Path) -> None:
    """prekのsystem hookがuv起動前に作業ツリーのvenvを指定する。"""
    config = (_REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "entry: uv run --project . --frozen pyfltr fast" in config
    assert "UV_PROJECT_ENVIRONMENT: .venv" in config
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "called.txt"
    uv = bin_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "${UV_PROJECT_ENVIRONMENT-<unset>}" > "$STUB_LOG"\n', encoding="utf-8")
    uv.chmod(0o755)
    hook_config = tmp_path / ".pre-commit-config.yaml"
    hook_config.write_text(
        """repos:
  - repo: local
    hooks:
      - id: pyfltr
        name: pyfltr
        entry: uv run --project . --frozen pyfltr fast
        env:
          UV_PROJECT_ENVIRONMENT: .venv
        language: system
        pass_filenames: false
        always_run: true
""",
        encoding="utf-8",
    )
    git = shutil.which("git")
    prek = _venv_executable(_REPOSITORY_ROOT / ".venv", "prek")
    assert git is not None
    assert prek.exists()
    initialized = subprocess.run([git, "init", "--quiet"], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert initialized.returncode == 0, initialized.stderr
    result = subprocess.run(
        [str(prek), "run", "--config", str(hook_config), "pyfltr", "--all-files"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": os.pathsep.join((str(bin_dir), os.environ["PATH"])),
            "STUB_LOG": str(log_path),
            "UV_PROJECT_ENVIRONMENT": "/parent-venv",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert log_path.read_text(encoding="utf-8").strip() == ".venv"


def _external_venv_with_sentinel(path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """uvの同期による消失を検出できる、外部用途のvenvを用意する。"""
    venv.EnvBuilder(with_pip=False).create(path)
    python = _venv_executable(path, "python")
    site_packages_result = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert site_packages_result.returncode == 0, site_packages_result.stderr
    site_packages = pathlib.Path(site_packages_result.stdout.strip())
    package = site_packages / "external_venv_sentinel"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 'preserved'\n", encoding="utf-8")
    distribution = site_packages / "external_venv_sentinel-0.0.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text("Name: external-venv-sentinel\nVersion: 0.0\n", encoding="utf-8")
    marker = path / "sentinel.txt"
    marker.write_text("外部用途の仮想環境\n", encoding="utf-8")
    return python, marker


@pytest.mark.skipif(
    any(shutil.which(command) is None for command in ("make", "mise", "uv")),
    reason="実Makefile検証に必要なmake、mise、uvが未導入",
)
def test_make_sync_preserves_external_project_environment(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """実Makefileのsyncは親指定の外部venvを同期先にしない。"""
    project = tmp_path / "project"
    shutil.copytree(
        _REPOSITORY_ROOT,
        project,
        ignore=shutil.ignore_patterns(".claude", ".git", ".pytest_cache", ".venv", "__pycache__", "node_modules", "target"),
    )
    parent_environment = tmp_path / "external-venv"
    parent_python, marker = _external_venv_with_sentinel(parent_environment)
    environment = {
        **host_environ(),
        "XDG_STATE_HOME": str(tmp_path / "mise-state"),
        "UV_FROZEN": "1",
        "UV_OFFLINE": "1",
        "UV_PROJECT_ENVIRONMENT": str(parent_environment),
    }
    trusted = subprocess.run(
        ["mise", "trust", str(project / "mise.toml")],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert trusted.returncode == 0, trusted.stderr

    synced = subprocess.run(
        ["make", "sync"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    assert synced.returncode == 0, synced.stderr
    assert (project / ".venv" / "pyvenv.cfg").exists()
    assert (project / "agent-toolkit" / ".venv" / "pyvenv.cfg").exists()
    assert marker.read_text(encoding="utf-8") == "外部用途の仮想環境\n"
    imported = subprocess.run(
        [str(parent_python), "-c", "import external_venv_sentinel; print(external_venv_sentinel.VALUE)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "preserved"
