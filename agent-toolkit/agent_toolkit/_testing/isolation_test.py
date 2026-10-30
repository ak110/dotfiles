"""`agent_toolkit/`配下のテストへ開発機の状態からの隔離が自動で適用されることを確かめる。"""

import inspect
import json
import os
import pathlib
import shlex
import subprocess
import sys
import textwrap
import typing
import venv

import pytest
from platformdirs import windows

from agent_toolkit._hooks import plugin_resources
from agent_toolkit._testing import isolation
from agent_toolkit._testing.loop_isolation_support import assert_loop_scope_isolation


def _external_venv_sentinel(path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """uvの同期先を誤指定した場合に削除される外部venvの識別物を作成する。"""
    venv.EnvBuilder(with_pip=False).create(path)
    python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    location = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        text=True,
        check=True,
    )
    package = pathlib.Path(location.stdout.strip()) / "isolation_sentinel"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 'preserved'\n", encoding="utf-8")
    distribution = package.parent / "isolation_sentinel-0.0.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text("Name: isolation-sentinel\nVersion: 0.0\n", encoding="utf-8")
    (distribution / "RECORD").write_text(
        "isolation_sentinel/__init__.py,,\nisolation_sentinel-0.0.dist-info/METADATA,,\n"
        "isolation_sentinel-0.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    marker = path / "sentinel.txt"
    marker.write_text("external\n", encoding="utf-8")
    return python, marker


def _assert_sentinel_preserved(python: pathlib.Path, marker: pathlib.Path) -> None:
    assert marker.read_text(encoding="utf-8") == "external\n"
    result = subprocess.run(
        [str(python), "-c", "import isolation_sentinel; print(isolation_sentinel.VALUE)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "preserved"


def test_development_state_isolated_by_default(tmp_path: pathlib.Path) -> None:
    """この起動範囲のテストは、何も設定しなくても開発機の状態から隔離される。"""
    isolation.assert_development_state_isolated(tmp_path)


def test_import_time_home_omits_delegated_plugin_root_and_restores_it(tmp_path, monkeypatch):
    """収集時に製品定数へ委譲元の資料rootを混ぜず、区間を抜けたら起動環境へ戻す。"""
    name = "AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT"
    value = str(tmp_path / "parent-plugin")
    monkeypatch.setenv(name, value)
    with isolation.import_time_home(tmp_path / "collection"):
        assert name not in os.environ
        assert plugin_resources.plugin_root() == pathlib.Path(__file__).resolve().parents[2]
    assert os.environ[name] == value


@pytest.mark.parametrize("scope", [None, "session"])
@pytest.mark.parametrize("configuration", ["pyproject.toml", "agent-toolkit/pyproject.toml"])
def test_function_loop_scope_prevents_environment_leak_between_tests(
    tmp_path: pathlib.Path, configuration: str, scope: str | None
) -> None:
    """両設定の実値で成功し、sessionへ戻す対照でテスト間の環境波及を検出する。"""
    root = pathlib.Path(__file__).resolve().parents[3]
    assert_loop_scope_isolation(tmp_path, configuration=root / configuration, scope=scope)


def test_windows_known_folders_use_isolated_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """共通fixtureはWindowsの実リゾルバーへ公開overrideを渡し、KnownFolder APIへ到達させない。"""
    root = pathlib.PureWindowsPath("C:/isolated-test")

    def reject_host_folder(_name: str) -> str:
        raise AssertionError("ホストのKnownFolder APIへ到達した")

    monkeypatch.setattr(windows, "_resolve_win_folder", reject_host_folder)
    with pytest.MonkeyPatch.context() as scoped:
        apply_fixture = typing.cast(
            typing.Callable[[pathlib.PurePath, pytest.MonkeyPatch, str], None],
            inspect.unwrap(isolation.isolate_development_state),
        )
        apply_fixture(root, scoped, "")
        directories = windows.Windows("probe", appauthor=False)
        for name in ("user_config_dir", "user_data_dir", "user_state_dir", "user_cache_dir", "site_data_dir"):
            assert pathlib.PureWindowsPath(getattr(directories, name)).is_relative_to(root), name


def test_commit_hook_context_does_not_escape_nested_test_repository(tmp_path: pathlib.Path) -> None:
    """実際のlinked worktreeのhookからpytestを起動しても、模擬repoのinit・addが元repoへ作用しない。"""
    environment = dict(os.environ)
    names = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.splitlines()
    for name in names:
        environment.pop(name, None)

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    git("init", "-q", "--initial-branch=main", str(main))
    (main / "kept.txt").write_text("元repoの内容\n", encoding="utf-8")
    git("-C", str(main), "add", ".")
    git("-C", str(main), "commit", "-q", "-m", "base")
    git("-C", str(main), "worktree", "add", "-q", "--detach", str(worktree), "HEAD")
    original_config = (main / ".git/config").read_bytes()
    original_tree = git("-C", str(worktree), "write-tree").stdout.strip()
    test_file = tmp_path / "nested_test.py"
    test_file.write_text(
        "import pathlib, subprocess\n"
        "def test_nested_repository(tmp_path):\n"
        "    inner = tmp_path / 'inner'\n"
        "    subprocess.run(['git', 'init', '-q', str(inner)], check=True)\n"
        "    (inner / 'probe.txt').write_text('nested\\n', encoding='utf-8')\n"
        "    subprocess.run(['git', '-C', str(inner), 'add', '.'], check=True)\n"
        "    result = subprocess.run(['git', '-C', str(inner), 'ls-files'], capture_output=True, text=True, check=True)\n"
        "    assert result.stdout.strip() == 'probe.txt'\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "agent_toolkit._testing.isolation",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:xdist",
        "-q",
        str(test_file),
    ]
    hook = main / ".git/hooks/pre-commit"
    result_file = tmp_path / "nested-result.json"
    runner = tmp_path / "hook_runner.py"
    runner.write_text(
        "import json, pathlib, subprocess\n"
        f"result = subprocess.run({command!r}, capture_output=True, text=True, "
        "encoding='utf-8', errors='replace', timeout=30)\n"
        f"pathlib.Path({str(result_file)!r}).write_text(json.dumps("
        "{'rc': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}), encoding='utf-8')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    hook.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, str(runner)]) + "\n", encoding="utf-8")
    hook.chmod(0o700)
    aborted = subprocess.run(
        ["git", "-C", str(worktree), "commit", "--allow-empty", "-m", "hook probe"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=45,
    )
    assert aborted.returncode == 1, aborted.stderr
    nested = json.loads(result_file.read_text(encoding="utf-8"))
    assert nested["rc"] == 0, nested
    assert (main / ".git/config").read_bytes() == original_config
    assert git("-C", str(worktree), "write-tree").stdout.strip() == original_tree


@pytest.mark.parametrize(
    ("platform", "environ", "expected"),
    [
        pytest.param(
            "linux",
            {"HOME": "/h"},
            {
                "npm_config_cache_dir": "/h/.cache/pnpm",
                "npm_config_store_dir": "/h/.local/share/pnpm/store",
                "COREPACK_HOME": "/h/.cache/node/corepack",
            },
            id="linux-home-only",
        ),
        pytest.param(
            "linux",
            {"HOME": "/h", "XDG_CACHE_HOME": "/c", "XDG_DATA_HOME": "/d"},
            {
                "npm_config_cache_dir": "/c/pnpm",
                "npm_config_store_dir": "/d/pnpm/store",
                "COREPACK_HOME": "/c/node/corepack",
            },
            id="linux-xdg",
        ),
        pytest.param(
            "linux",
            {"HOME": "/h", "PNPM_HOME": "/p", "XDG_DATA_HOME": "/d"},
            {
                "npm_config_cache_dir": "/h/.cache/pnpm",
                "npm_config_store_dir": "/p/store",
                "COREPACK_HOME": "/h/.cache/node/corepack",
            },
            id="linux-pnpm-home",
        ),
        pytest.param(
            "linux",
            {"HOME": "/h", "npm_config_cache_dir": "/x", "npm_config_store_dir": "/y", "COREPACK_HOME": "/z"},
            {"npm_config_cache_dir": "/x", "npm_config_store_dir": "/y", "COREPACK_HOME": "/z"},
            id="explicit-values",
        ),
        pytest.param(
            "darwin",
            {"HOME": "/Users/u"},
            {
                "npm_config_cache_dir": "/Users/u/Library/Caches/pnpm",
                "npm_config_store_dir": "/Users/u/Library/pnpm/store",
                "COREPACK_HOME": "/Users/u/.cache/node/corepack",
            },
            id="darwin",
        ),
        pytest.param(
            "win32",
            {"USERPROFILE": r"C:\Users\u", "LOCALAPPDATA": r"C:\Users\u\AppData\Local"},
            {
                "npm_config_cache_dir": r"C:\Users\u\AppData\Local\pnpm-cache",
                "npm_config_store_dir": r"C:\Users\u\AppData\Local\pnpm\store",
                "COREPACK_HOME": r"C:\Users\u\AppData\Local\node\corepack",
            },
            id="win32",
        ),
    ],
)
def test_package_cache_environ_follows_tool_rules(platform: str, environ: dict[str, str], expected: dict[str, str]) -> None:
    """pnpmの公式資料とcorepackの実装が定める保存先の規則どおりに、ホストの位置を求める。"""
    assert isolation.package_cache_environ(environ, platform) == expected


@pytest.mark.parametrize("explicit", [False, True], ids=["uv-default", "uv-explicit"])
def test_uv_cache_is_resolved_before_state_isolation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, host_environ, explicit: bool
) -> None:
    """uv自身の解決結果を使い、隔離したXDGキャッシュ先へ取得物を向けない。"""
    captured = {name: value for name, value in host_environ().items() if name in isolation.PACKAGE_CACHE_ENVIRONMENT_NAMES}
    captured.pop("UV_CACHE_DIR", None)
    if explicit:
        captured["UV_CACHE_DIR"] = str(tmp_path / "download-cache")
    monkeypatch.setattr(isolation, "_HOST_PACKAGE_CACHE_ENVIRON", captured)
    environ = host_environ()
    environ.pop("UV_CACHE_DIR", None)
    environ.update(captured)
    expected = subprocess.run(["uv", "cache", "dir"], env=environ, capture_output=True, text=True, check=True).stdout.strip()
    isolation.host_package_cache_environ.cache_clear()
    try:
        assert isolation.host_package_cache_environ()["UV_CACHE_DIR"] == expected
        assert expected != str(pathlib.Path(os.environ["XDG_CACHE_HOME"]) / "uv")
        isolation.assert_development_state_isolated(tmp_path)
    finally:
        isolation.host_package_cache_environ.cache_clear()


@pytest.mark.usefixtures("share_package_caches")
def test_shared_cache_child_keeps_development_state_isolated(tmp_path: pathlib.Path) -> None:
    """実子プロセスへ取得物だけが渡り、設定・private-notes・出力状態は隔離される。"""
    result = subprocess.run(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        capture_output=True,
        text=True,
        check=True,
    )
    observed = json.loads(result.stdout)
    assert observed["UV_CACHE_DIR"] == isolation.host_package_cache_environ()["UV_CACHE_DIR"]
    assert pathlib.Path(observed["HOME"]).is_relative_to(tmp_path)
    assert pathlib.Path(observed["XDG_CONFIG_HOME"]).is_relative_to(tmp_path)
    assert pathlib.Path(observed["XDG_CACHE_HOME"]).is_relative_to(tmp_path)
    assert pathlib.Path(observed["AGENT_TOOLKIT_PRIVATE_NOTES"]).is_relative_to(tmp_path)
    assert observed["MISE_CEILING_PATHS"] == str(tmp_path)
    for name in ("WIN_PD_OVERRIDE_APPDATA", "WIN_PD_OVERRIDE_LOCAL_APPDATA", "WIN_PD_OVERRIDE_COMMON_APPDATA"):
        assert pathlib.Path(observed[name]).is_relative_to(tmp_path)
    isolation.assert_development_state_isolated(tmp_path)


def test_child_platformdirs_resolves_inside_isolation(tmp_path: pathlib.Path) -> None:
    """子プロセスの実platformdirs解決先が、書込前から全て専用領域内にある。"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, platformdirs; d=platformdirs.PlatformDirs('probe', appauthor=False); "
            "print(json.dumps([getattr(d, name) for name in "
            "('user_config_dir','user_data_dir','user_state_dir','user_cache_dir')]))",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert all(pathlib.Path(value).is_relative_to(tmp_path) for value in json.loads(result.stdout))


@pytest.mark.skipif(sys.platform == "win32", reason="最低版Gitの差し替えはPOSIXのCIで使う")
def test_test_git_directory_resolves_specified_executable(tmp_path: pathlib.Path) -> None:
    """指定したGitの実行ファイルを`git`の名前で解決させ、起動できない指定はテストの開始前に拒否する。"""
    fake = tmp_path / "bin" / "git-2.34.1"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\necho "git version 2.34.1"\n', encoding="utf-8")
    fake.chmod(0o755)
    directory = isolation.build_test_git_directory(str(fake), tmp_path / "test-git")
    result = subprocess.run(
        ["git", "--version"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PATH": os.pathsep.join((str(directory), os.environ["PATH"]))},
    )
    assert result.stdout.strip() == "git version 2.34.1"

    broken = tmp_path / "bin" / "broken"
    broken.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    broken.chmod(0o755)
    for value in ("git", str(tmp_path / "missing"), str(broken)):
        with pytest.raises(pytest.UsageError, match=isolation.TEST_GIT_ENVIRONMENT_NAME):
            isolation.build_test_git_directory(value, tmp_path / f"rejected-{len(value)}")


def test_parent_update_and_config_override_environment_removed(tmp_path: pathlib.Path) -> None:
    """親の`update-dotfiles`の実行状態と設定上書きを継承した子pytestでも、試験はそれらを持たない状態で始まる。

    `update-dotfiles`はCodex daemonの再起動を経て、エージェントのシェルへこれらの変数を残す。
    継承されると単独の公開操作を試すテストが更新処理配下の分岐へ入るため、共通隔離で除く。
    """
    probe = tmp_path / "probe_test.py"
    probe.write_text(
        textwrap.dedent("""\
            import os, pathlib
            from agent_toolkit._testing import isolation

            def test_probe(tmp_path):
                isolation.assert_development_state_isolated(tmp_path)
                for name in ("UPDATE_DOTFILES_RUN_ID", "DOTFILES_PRUNE", "DOTFILES_CODEX_DAEMON_AUTO_RESTART",
                             "AGENT_TOOLKIT_CONFIG_DOTFILES_MODE", "AGENT_TOOLKIT_CONFIG_EXAMPLE",
                             "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "AGENT_TOOLKIT_RUNTIME_ENVIRONMENT",
                             "AGENT_TOOLKIT_CALLER_UV_PROJECT_ENVIRONMENT", "AGENT_TOOLKIT_LAUNCH_RESTORE"):
                    assert name not in os.environ, name
                assert str(pathlib.Path(os.environ["PARENT_VENV_BIN"])) not in os.environ["PATH"].split(os.pathsep)
        """),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "UPDATE_DOTFILES_RUN_ID": "parent-run",
        "DOTFILES_PRUNE": "1",
        "DOTFILES_CODEX_DAEMON_AUTO_RESTART": "1",
        "AGENT_TOOLKIT_CONFIG_DOTFILES_MODE": "minimal",
        "AGENT_TOOLKIT_CONFIG_EXAMPLE": "parent",
        "UV_PROJECT_ENVIRONMENT": str(tmp_path / "parent-venv"),
        "VIRTUAL_ENV": str(tmp_path / "parent-venv"),
        "AGENT_TOOLKIT_RUNTIME_ENVIRONMENT": "parent-runtime",
        "AGENT_TOOLKIT_CALLER_UV_PROJECT_ENVIRONMENT": "parent-caller-venv",
        "AGENT_TOOLKIT_LAUNCH_RESTORE": "parent-restore",
        "PARENT_VENV_BIN": str(tmp_path / "parent-venv" / "bin"),
    }
    environment["PATH"] = os.pathsep.join((environment["PARENT_VENV_BIN"], environment["PATH"]))
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-p", isolation.__name__, str(probe)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_host_environ_excludes_parent_venv_operation_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """ホスト環境の復元処理でも、親の仮想環境同期先を除外する。"""
    names = (
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
        "AGENT_TOOLKIT_RUNTIME_ENVIRONMENT",
        "AGENT_TOOLKIT_CALLER_UV_PROJECT_ENVIRONMENT",
        "AGENT_TOOLKIT_LAUNCH_RESTORE",
    )
    for name in names:
        monkeypatch.setenv(name, "parent-venv")
    assert all(name not in isolation.build_host_environ() for name in names)


def test_host_environ_excludes_parent_venv_bin_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """host復元でも親venvのbin/ScriptsをPATHへ戻さない。"""
    monkeypatch.setattr(isolation, "_HOST_VENV_BINARIES", ("/parent/bin", "C:/parent/Scripts"))
    monkeypatch.setitem(isolation._HOST_ENVIRON, "PATH", "/keep:/parent/bin:/other")  # pylint: disable=protected-access
    assert "/parent/bin" not in isolation.build_host_environ()["PATH"].split(os.pathsep)


def test_restore_host_environment_excludes_parent_venv_operation_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """ホスト復元は親の仮想環境指定と実行ディレクトリを再導入しない。"""
    monkeypatch.setattr(isolation, "_HOST_VENV_BINARIES", ("/parent/bin",))
    monkeypatch.setitem(isolation._HOST_ENVIRON, "PATH", "/keep:/parent/bin:/other")  # pylint: disable=protected-access
    for name in isolation._VENV_ENVIRONMENT_NAMES:  # pylint: disable=protected-access
        monkeypatch.setenv(name, "parent-venv")

    isolation.restore_host_environment(monkeypatch)

    assert all(name not in os.environ for name in isolation._VENV_ENVIRONMENT_NAMES)  # pylint: disable=protected-access
    assert "/parent/bin" not in os.environ["PATH"].split(os.pathsep)


@pytest.mark.parametrize("route", ["fixture", "host_environ", "restore_host_environment"])
def test_child_uv_uses_test_project_after_fixture_and_host_restoration(
    tmp_path: pathlib.Path, host_environ: typing.Callable[[], dict[str, str]], route: str
) -> None:
    """親の指定を継承した子pytestでも、3つの復元経路から実行するuvは外部venvを変更しない。"""
    external = tmp_path / "external"
    sentinel_python, marker = _external_venv_sentinel(external)
    executable = "uv.exe" if os.name == "nt" else "uv"
    uv = next(
        candidate
        for directory in host_environ()["PATH"].split(os.pathsep)
        if (candidate := pathlib.Path(directory) / executable).is_file() and candidate.resolve().stem != "mise"
    )
    probe = tmp_path / "uv_probe_test.py"
    probe.write_text(
        textwrap.dedent("""\
            import os, pathlib, subprocess, sys
            from agent_toolkit._testing import isolation

            def test_uv(tmp_path, monkeypatch):
                route = os.environ["ISOLATION_PROBE_ROUTE"]
                if route == "host_environ":
                    environment = isolation.build_host_environ()
                elif route == "restore_host_environment":
                    isolation.restore_host_environment(monkeypatch)
                    environment = dict(os.environ)
                else:
                    environment = dict(os.environ)
                project = tmp_path / "project"
                project.mkdir()
                (project / "pyproject.toml").write_text(
                    '[project]\\nname = "probe"\\nversion = "0"\\nrequires-python = ">=3.15"\\n',
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [os.environ["ISOLATION_PROBE_UV"], "sync", "--offline", "--no-install-project",
                     "--project", str(project), "--python", sys.executable],
                    # 新規fixtureにはlockが無い。CIの凍結条件をこの初期同期だけ解除する。
                    cwd=project, env={**environment, "UV_FROZEN": "0"},
                    capture_output=True, text=True, check=False, timeout=60,
                )
                assert result.returncode == 0, result.stderr
                assert (project / ".venv" / "pyvenv.cfg").is_file()
        """),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "UV_FROZEN": "1",
        "ISOLATION_PROBE_UV": str(uv),
        "ISOLATION_PROBE_ROUTE": route,
        **dict.fromkeys(isolation._VENV_ENVIRONMENT_NAMES, str(external)),  # pylint: disable=protected-access
    }
    parent_bin = external / ("Scripts" if os.name == "nt" else "bin")
    environment["PATH"] = str(parent_bin) + os.pathsep + environment["PATH"]
    environment.pop("PYTEST_ADDOPTS", None)
    pytest_options = ["-v", "-p", "no:cacheprovider", "-o", "addopts=", "-p", isolation.__name__]
    command = [sys.executable, "-m", "pytest", *pytest_options, str(probe)]
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_sentinel_preserved(sentinel_python, marker)
