"""実pytestの収集を通し、自動fixtureの登録とディレクトリ境界を確かめる。"""

import asyncio
import contextlib
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace
from typing import Any, cast

import claude_agent_sdk
import psutil
import pytest

from agent_toolkit._agents_server import backends
from agent_toolkit._testing import pytest_plugin, slot_yield

# 読込済みrootと公開設定から求める対象rootを正負で直接比較する。
# pylint: disable=protected-access


class _Config:
    def __init__(self, rootpath: pathlib.Path) -> None:
        self.rootpath = rootpath


def _config(rootpath: pathlib.Path) -> pytest.Config:
    """rootpathだけを使う読込元比較へ、公開Configと同じ静的型で渡す。"""
    return cast(pytest.Config, _Config(rootpath))


@pytest.mark.parametrize("engine", sorted(backends.SUPPORTED_ENGINES))
@pytest.mark.asyncio
async def test_forbid_backend_process_rejects_every_supported_backend(
    tmp_path: pathlib.Path, _forbid_backend_process: None, engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全engineのbackendを生成して、実CLIへ届く前に起動が遮断されることを確かめる。"""

    def unexpected_client(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("遮断fixtureがSDKへ到達した")

    async def unexpected_process(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("遮断fixtureがイベントループの子プロセス生成へ到達した")

    # 検証対象のguardが壊れた場合も、さらに下層で実SDK/CLIへの到達を止める。
    # 期待するAssertionErrorと異なる例外にして、guardの欠落を成功と数えない。
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", unexpected_client)
    monkeypatch.setattr(asyncio.get_running_loop(), "subprocess_exec", unexpected_process)
    backend = backends.create_backend(
        engine,
        {},
        asyncio.Condition(),
        expire_session=lambda _session_id: None,
        root_session_id=None,
        log_directory=tmp_path,
    )
    try:
        with pytest.raises(AssertionError, match="実backendの子プロセス"):
            await backend.start("遮断確認", str(tmp_path), model="test/model", effort="low" if engine == "codex" else None)
    finally:
        await backend.close()


def test_target_plugin_root_limits_import_check_to_public_target(tmp_path: pathlib.Path) -> None:
    """外部fixtureのrootは比較せず、dotfilesとplugin rootは対象を一意にする。"""
    plugin = tmp_path / "dotfiles" / "agent-toolkit"
    (plugin / "agent_toolkit").mkdir(parents=True)
    (plugin / "agent_toolkit" / "__init__.py").touch()
    assert pytest_plugin._target_plugin_root(_config(plugin)) == plugin
    assert pytest_plugin._target_plugin_root(_config(plugin.parent)) == plugin
    assert pytest_plugin._target_plugin_root(_config(tmp_path / "external")) is None


def test_target_plugin_root_accepts_renamed_standalone_package(tmp_path: pathlib.Path) -> None:
    """standalone checkoutはディレクトリ名に関係なく実在するパッケージで判定する。"""
    standalone = tmp_path / "cached-agent-toolkit-vnext"
    (standalone / "agent_toolkit").mkdir(parents=True)
    (standalone / "agent_toolkit" / "__init__.py").touch()
    assert pytest_plugin._target_plugin_root(_config(standalone)) == standalone


@pytest.mark.parametrize("target_kind", ["plugin", "dotfiles"])
def test_validate_import_root_accepts_public_target_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    """plugin rootとdotfiles rootのどちらからも、対象内の読込を受理する。"""
    plugin = tmp_path / "dotfiles" / "agent-toolkit"
    loaded = plugin / "agent_toolkit" / "__init__.py"
    loaded.parent.mkdir(parents=True)
    loaded.touch()
    monkeypatch.setattr(pytest_plugin.agent_toolkit, "__file__", str(loaded))
    pytest_plugin._validate_import_root(_config(plugin if target_kind == "plugin" else plugin.parent))


@pytest.mark.parametrize("target_kind", ["plugin", "dotfiles"])
def test_validate_import_root_rejects_other_plugin(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, target_kind: str
) -> None:
    """公開対象とは異なる配布rootの読込を、双方のパスを含む診断で拒否する。"""
    target = tmp_path / "dotfiles" / "agent-toolkit"
    foreign = tmp_path / "foreign" / "agent_toolkit" / "__init__.py"
    (target / "agent_toolkit").mkdir(parents=True)
    (target / "agent_toolkit" / "__init__.py").touch()
    foreign.parent.mkdir(parents=True)
    foreign.touch()
    monkeypatch.setattr(pytest_plugin.agent_toolkit, "__file__", str(foreign))
    root = target if target_kind == "plugin" else target.parent
    with pytest.raises(pytest.UsageError) as error:
        pytest_plugin._validate_import_root(_config(root))
    message = str(error.value)
    assert "loaded=" in message and "target=" in message
    assert "PYTHONPATH と UV_PYTHON を解除し" in message
    assert "対象worktreeをカレントディレクトリにして" in message
    assert "uv run --frozen pytest <対象試験>" in message


def test_validate_import_root_skips_external_fixture_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """外部fixtureのpytestはagent-toolkit読込元の比較対象にしない。"""
    foreign = tmp_path / "foreign" / "agent_toolkit" / "__init__.py"
    foreign.parent.mkdir(parents=True)
    foreign.touch()
    monkeypatch.setattr(pytest_plugin.agent_toolkit, "__file__", str(foreign))
    pytest_plugin._validate_import_root(_config(tmp_path / "fixture"))


def test_validate_import_root_skips_name_only_external_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """名前だけがagent-toolkitの外部fixtureはパッケージ実体がなければ対象外にする。"""
    external = tmp_path / "agent-toolkit"
    external.mkdir()
    foreign = tmp_path / "foreign" / "agent_toolkit" / "__init__.py"
    foreign.parent.mkdir(parents=True)
    foreign.touch()
    monkeypatch.setattr(pytest_plugin.agent_toolkit, "__file__", str(foreign))
    pytest_plugin._validate_import_root(_config(external))


@pytest.mark.parametrize("mode", ["pass", "fail", "interrupt", "xdist", "explicit"])
def test_pytest_owns_fixture_directory_and_reclaims_children(tmp_path: pathlib.Path, mode: str) -> None:
    """公開pytestの正常・失敗・中断とworker終了で専用領域を回収し、証拠と明示領域を保つ。"""
    evidence = tmp_path / "evidence.json"
    probe = tmp_path / "probe_test.py"
    probe.write_text(
        textwrap.dedent(f"""\
            import json, pathlib, subprocess, sys
            import pytest
            from pyfltr.command import execution_slot

            def test_probe(tmp_path, pytestconfig):
                owner = execution_slot.read_owner()
                assert owner is not None
                child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
                pathlib.Path({str(evidence)!r}).write_text(json.dumps({{
                    'basetemp': str(pytestconfig.option.basetemp),
                    'fixture': str(tmp_path), 'pid': child.pid,
                }}), encoding='utf-8')
                {"raise KeyboardInterrupt" if mode == "interrupt" else "assert False" if mode == "fail" else "assert True"}
        """),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYFLTR_CACHE_DIR"] = str(tmp_path / "pyfltr")
    argv = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-p", pytest_plugin.__name__]
    if mode == "xdist":
        argv.extend(["-p", "xdist.plugin", "-n", "2"])
    explicit = tmp_path / "caller-owned"
    if mode == "explicit":
        argv.extend(["--basetemp", str(explicit)])
    result = subprocess.run(
        [*argv, "--junitxml", str(tmp_path / "junit.xml"), str(probe)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == {"fail": 1, "interrupt": 2}.get(mode, 0), result.stdout + result.stderr
    observed = json.loads(evidence.read_text(encoding="utf-8"))
    assert evidence.exists() and (tmp_path / "junit.xml").exists()
    assert pathlib.Path(observed["basetemp"]).exists() == (mode == "explicit")
    if mode != "explicit":
        base = pathlib.Path(observed["basetemp"])
        # xdist側ではworkerの領域が記録される。controllerが所有する最上位領域も回収される。
        directory = next(path for path in (base, *base.parents) if path.name == "fixtures")
        assert not directory.parent.exists()
    with contextlib.suppress(psutil.NoSuchProcess):
        assert psutil.Process(observed["pid"]).status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)


@pytest.mark.parametrize("workers", [0, 2])
def test_concurrent_controllers_keep_other_fixtures_and_saved_outputs(tmp_path: pathlib.Path, workers: int) -> None:
    """別controllerが待機中の領域を残し、自分の領域だけを回収して両出力を保持する。"""
    probe = tmp_path / "concurrent_test.py"
    probe.write_text(
        textwrap.dedent("""\
            import json, os, socket

            def test_probe(tmp_path, pytestconfig):
                (tmp_path / 'payload').write_text('実行中', encoding='utf-8')
                if os.environ.get('PROBE_PORT'):
                    with socket.create_connection(('127.0.0.1', int(os.environ['PROBE_PORT'])), timeout=30) as peer:
                        peer.sendall((json.dumps({'base': str(pytestconfig.option.basetemp)}) + '\\n').encode())
                        assert peer.recv(1) == b'x'
                print('保存する標準出力')
                import sys
                print('保存する標準エラー', file=sys.stderr)
        """),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # この検証は所有領域の独立性を対象とするため、検証枠だけをcontrollerごとに分離する。
    environment["PYFLTR_CACHE_DIR"] = str(tmp_path / "second-slot")
    argv = [sys.executable, "-m", "pytest", "-q", "-s", "-o", "addopts=", "-p", pytest_plugin.__name__]
    if workers:
        argv.extend(["-p", "xdist.plugin", "-n", str(workers)])
    argv.append(str(probe))
    saved_paths = [(tmp_path / f"controller-{number}.stdout", tmp_path / f"controller-{number}.stderr") for number in (0, 1)]
    with contextlib.ExitStack() as stack, socket.socket() as listener:
        streams = [
            (stack.enter_context(output.open("w", encoding="utf-8")), stack.enter_context(error.open("w", encoding="utf-8")))
            for output, error in saved_paths
        ]
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(30)
        first_environment = {
            **environment,
            "PROBE_PORT": str(listener.getsockname()[1]),
            "PYFLTR_CACHE_DIR": str(tmp_path / "first-slot"),
        }
        with subprocess.Popen(
            argv,
            cwd=tmp_path,
            env=first_environment,
            stdout=streams[0][0],
            stderr=streams[0][1],
            text=True,
            encoding="utf-8",
        ) as first:
            try:
                peer, _address = listener.accept()
                with peer:
                    peer.settimeout(30)
                    data = b""
                    while not data.endswith(b"\n"):
                        part = peer.recv(4096)
                        assert part, "最初のcontrollerが所有領域の通知前に終了した"
                        data += part
                    base = pathlib.Path(json.loads(data)["base"])
                    assert base.exists()
                    second = subprocess.run(
                        argv,
                        cwd=tmp_path,
                        env=environment,
                        stdout=streams[1][0],
                        stderr=streams[1][1],
                        text=True,
                        encoding="utf-8",
                        timeout=60,
                        check=False,
                    )
                    assert second.returncode == 0, [path.read_text(encoding="utf-8") for path in saved_paths[1]]
                    assert base.exists()
                    peer.sendall(b"x")
                first.wait(timeout=60)
                assert first.returncode == 0, [path.read_text(encoding="utf-8") for path in saved_paths[0]]
            finally:
                if first.poll() is None:
                    first.terminate()
                    first.wait(timeout=30)
    assert not base.exists()
    for output, error in saved_paths:
        assert "1 passed" in output.read_text(encoding="utf-8")
        assert "保存する標準エラー" in error.read_text(encoding="utf-8")


@pytest.mark.parametrize("parent_first", [False, True])
def test_collection_order_preserves_isolation_and_overrides(tmp_path: pathlib.Path, parent_first: bool) -> None:
    """親を間に配置する収集でも7件が適用され、範囲外・個別差し替え・実物待機への復帰を保つ。"""
    package = tmp_path / "sample"
    for suffix in ("", "_atk", "_atk/managed_temp", "_atk/wi", "_atk/wi/mutations"):
        directory = package / suffix
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").touch()
    # 明示要求型fixtureも起動時プラグインから使い、自動fixtureの適用先だけを試験用パッケージへ向ける。
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent("""\
            import os
            import pathlib
            from agent_toolkit._testing import pytest_plugin
            from agent_toolkit._hooks import notice

            def pytest_configure(config):
                pytest_plugin._PACKAGE_ROOT = pathlib.Path(__file__).parent / "sample"
                os.environ["FORCE_PROMPT_CACHING_5M"] = "outside"
                os.environ["AI_AGENT"] = "outside"
                os.environ["XDG_STATE_HOME"] = str(pathlib.Path(__file__).parent / "outside-state")
                notice._warning_context = {"session_id": "outside", "blocks": []}
        """),
        encoding="utf-8",
    )
    (tmp_path / "probe.py").write_text(
        textwrap.dedent("""\
            import os
            import pathlib
            import shutil
            import tempfile
            from agent_toolkit._common import codex_models
            from agent_toolkit._hooks import notice, transcript_scan
            from agent_toolkit._atk.managed_temp import paths as registry

            original_models = codex_models.list_models
            original_wait = transcript_scan.wait_for_end_turn
            original_terminal = shutil.get_terminal_size
            original_state = registry._state_root_path

            def common():
                assert "FORCE_PROMPT_CACHING_5M" not in os.environ
                assert notice._warning_context == {"session_id": "", "blocks": []}
                assert codex_models.list_models is not original_models
                assert codex_models.list_models()
                assert transcript_scan.wait_for_end_turn is not original_wait
                assert shutil.get_terminal_size().columns == 200

            def outside():
                assert os.environ["FORCE_PROMPT_CACHING_5M"] == "outside"
                assert notice._warning_context["session_id"] == "outside"
                assert codex_models.list_models is original_models
                assert transcript_scan.wait_for_end_turn is original_wait
                assert shutil.get_terminal_size is original_terminal
                unchanged_subdirectories()

            def unchanged_subdirectories():
                assert registry._state_root_path is original_state
                assert os.environ["AI_AGENT"] == "outside"
                assert pathlib.Path(os.environ["XDG_STATE_HOME"]).name == "outside-state"

            def managed(tmp_path):
                common()
                assert registry._state_root_path() == tmp_path / "external-state"
                assert registry._temp_root_path() == tmp_path / "managed-temp"
                assert os.environ["AI_AGENT"] == "outside"

            def mutations(tmp_path):
                common()
                assert "AI_AGENT" not in os.environ
                assert pathlib.Path(tempfile.gettempdir()) == tmp_path / "temp"
                assert pathlib.Path(os.environ["XDG_STATE_HOME"]) == tmp_path / "state"
                assert registry._state_root_path is original_state
        """),
        encoding="utf-8",
    )
    # probeを収集時に読み込んで差し替え前の関数を保持する。
    common = "import probe\n\ndef test_common():\n    probe.common()\n    probe.unchanged_subdirectories()\n"
    managed = "import probe\n\ndef test_managed(tmp_path):\n    probe.managed(tmp_path)\n"
    mutations = "import probe\n\ndef test_mutations(tmp_path):\n    probe.mutations(tmp_path)\n"
    files = {
        "sample/first_test.py": common,
        "sample/_atk/managed_temp/first_test.py": managed,
        "sample/_atk/wi/mutations/first_test.py": mutations,
        "outside_test.py": "import probe\n\ndef test_outside():\n    probe.outside()\n",
        "sample/last_test.py": common,
        "sample/_atk/managed_temp/last_test.py": managed,
        "sample/_atk/wi/mutations/last_test.py": mutations,
        "sample/override_test.py": textwrap.dedent("""\
            import os
            import shutil
            import probe
            from agent_toolkit._hooks import transcript_scan

            def test_overrides(monkeypatch, real_end_turn_wait):
                monkeypatch.setattr(shutil, "get_terminal_size", lambda **kwargs: os.terminal_size((73, 24)))
                assert shutil.get_terminal_size().columns == 73
                assert transcript_scan.wait_for_end_turn is probe.original_wait
        """),
    }
    for relative, content in files.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")
    order = list(files)
    if parent_first:
        order.remove("outside_test.py")
        order.insert(0, "outside_test.py")
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-p",
            "agent_toolkit._testing.pytest_plugin",
            *order,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passed" in result.stdout


def _run_child_pytest(tmp_path: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """起動時プラグインだけを読む子pytestを`tmp_path`で実行し、両出力と終了コードを返す。"""
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYFLTR_CACHE_DIR"] = str(tmp_path / "pyfltr")
    argv = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-p", pytest_plugin.__name__]
    return subprocess.run(
        [*argv, *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def test_release_keeps_interpreter_resource_tracker(tmp_path: pathlib.Path) -> None:
    """spawnの資源を終了まで保持する試験の後も、未解放の資源を報告する警告を出力せず、試験が残した子だけを回収する。

    共通回収がmultiprocessingのresource_trackerを停止すると、終了時の資源解放がtrackerを再起動し、
    `process died unexpectedly, relaunching`を標準エラーへ出力する。
    """
    evidence = tmp_path / "evidence.json"
    (tmp_path / "tracker_test.py").write_text(
        textwrap.dedent(f"""\
            import json, multiprocessing, pathlib, subprocess, sys
            from multiprocessing import resource_tracker

            KEPT = []

            def test_probe():
                KEPT.append(multiprocessing.get_context("spawn").Semaphore())
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
                pathlib.Path({str(evidence)!r}).write_text(json.dumps({{
                    "tracker": resource_tracker._resource_tracker._pid, "child": child.pid,
                }}), encoding="utf-8")
        """),
        encoding="utf-8",
    )
    result = _run_child_pytest(tmp_path, "tracker_test.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "relaunching" not in result.stderr, result.stderr
    observed = json.loads(evidence.read_text(encoding="utf-8"))
    assert observed["tracker"] is not None
    # 終了済みの子はzombieからdeadを経て消えるため、照会時点によらず終了状態を確かめる。
    with contextlib.suppress(psutil.NoSuchProcess):
        assert psutil.Process(observed["child"]).status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)


@pytest.mark.parametrize("workers", [0, 2])
def test_collection_binds_import_time_home_to_basetemp(tmp_path: pathlib.Path, workers: int) -> None:
    """収集時にimportされたモジュールが確定するホームは、実行環境のホームでなくbasetemp配下を指す。

    試験関数ごとの隔離は収集の後に始まるため、収集区間の隔離が無いとimport時の定数が実ホームを指す。
    """
    (tmp_path / "home_probe.py").write_text("import pathlib\n\nHOME = pathlib.Path.home()\n", encoding="utf-8")
    (tmp_path / "home_test.py").write_text(
        textwrap.dedent(f"""\
            import os, pathlib
            import home_probe

            def test_probe(pytestconfig):
                assert home_probe.HOME == pathlib.Path(pytestconfig.option.basetemp) / "import-time-home"
                assert home_probe.HOME != pathlib.Path({str(pathlib.Path.home())!r})
                assert pathlib.Path.home() == pathlib.Path({str(pathlib.Path.home())!r})
        """),
        encoding="utf-8",
    )
    arguments = ["-p", "xdist.plugin", "-n", str(workers)] if workers else []
    result = _run_child_pytest(tmp_path, *arguments, "home_test.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_agents_server_logging_reclaimed_before_next_test(tmp_path: pathlib.Path) -> None:
    """ログ設定を呼んだ試験の後でも、後続の試験の警告は閉じた出力先へ書かれず有効な取り込み先へ届く。

    標準エラー向けhandlerは構築時の`sys.stderr`（試験中の取り込み用stream）を保持する。
    回収しないと、後続の警告がそのhandlerで`I/O operation on closed file`となり`Logging error`を出力する。
    """
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").touch()
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent("""\
            import pathlib
            from agent_toolkit._testing import pytest_plugin

            def pytest_configure(config):
                pytest_plugin._PACKAGE_ROOT = pathlib.Path(__file__).parent / "sample"
        """),
        encoding="utf-8",
    )
    (package / "logging_test.py").write_text(
        textwrap.dedent("""\
            import logging
            from agent_toolkit._agents_server import logging_config
            from agent_toolkit._common import state_paths

            SERVER = logging.getLogger("agent-toolkit.agents-server")
            BEFORE = (tuple(SERVER.handlers), SERVER.level, SERVER.propagate)

            def test_a_configures_logging(capsys, monkeypatch, tmp_path):
                monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
                logging_config.configure_logging()
                assert SERVER.handlers

            def test_b_later_warning_reaches_capture(capsys, caplog):
                assert (tuple(SERVER.handlers), SERVER.level, SERVER.propagate) == BEFORE
                logging.getLogger("agent-toolkit.agents-server.probe").warning("後続の警告")
                assert "Logging error" not in capsys.readouterr().err
                assert "後続の警告" in caplog.text
        """),
        encoding="utf-8",
    )
    result = _run_child_pytest(tmp_path, "-v", "sample/logging_test.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_pytest_notifies_once_while_waiting_for_slot_held_by_other_process(tmp_path: pathlib.Path) -> None:
    """別プロセスが実行枠を保持する間に起動したpytestは、保持者の情報を含む待機の通知を標準エラーへ1回出す。"""
    probe = tmp_path / "probe_test.py"
    probe.write_text("def test_probe():\n    pass\n", encoding="utf-8")
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(
        textwrap.dedent("""\
            import pathlib, sys, time
            from pyfltr.command import execution_slot

            with execution_slot.hold(label="holder-probe"):
                print("held", flush=True)
                release = pathlib.Path(sys.argv[1])
                deadline = time.monotonic() + 60
                while not release.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
        """),
        encoding="utf-8",
    )
    release = tmp_path / "release"
    error_path = tmp_path / "child.stderr"
    environment = {**os.environ, "PYFLTR_CACHE_DIR": str(tmp_path / "pyfltr"), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    environment.pop("PYTEST_ADDOPTS", None)
    child_argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="]
    child_argv += ["-p", pytest_plugin.__name__, str(probe)]
    with (
        subprocess.Popen(
            [sys.executable, str(holder_script), str(release)],
            env=environment,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as holder,
        error_path.open("w", encoding="utf-8") as error_stream,
    ):
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            with subprocess.Popen(
                child_argv,
                cwd=tmp_path,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=error_stream,
                text=True,
                encoding="utf-8",
            ) as child:
                deadline = time.monotonic() + 60
                # 子pytestの待機の開始を確かめてから保持を解く。
                while "実行枠を保持している" not in error_path.read_text(encoding="utf-8"):
                    assert child.poll() is None, error_path.read_text(encoding="utf-8")
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                release.touch()
                output, _ = child.communicate(timeout=60)
        finally:
            release.touch()
            holder.wait(timeout=60)
    errors = error_path.read_text(encoding="utf-8")
    assert child.returncode == 0, (output, errors)
    assert errors.count("実行枠を保持している") == 1
    assert f"label=holder-probe、pid={holder.pid}" in errors
    assert "待機時間は試験の処理時間に含まれない" in errors


def test_test_shards_partition_collected_tests_across_runs(tmp_path: pathlib.Path) -> None:
    """全分割の実行結果を合わせると、xdistのworkerを含めて各試験がちょうど1回実行される。"""
    (tmp_path / "shard_test.py").write_text(
        textwrap.dedent("""\
            import pytest

            @pytest.mark.parametrize("value", range(30))
            def test_value(value):
                pass

            def test_single():
                pass
        """),
        encoding="utf-8",
    )
    expected = {f"shard_test.py::test_value[{value}]" for value in range(30)} | {"shard_test.py::test_single"}
    executed: list[set[str]] = []
    # 子pytestの起動回数を抑えるため2分割で確かめ、2番目はworker間で同じ集合を選ぶことも見るためxdistで実行する。
    for index, workers in ((1, []), (2, ["-p", "xdist.plugin", "-n", "2"])):
        result = _run_child_pytest(tmp_path, "-rA", *workers, f"--test-shard={index}/2", "shard_test.py")
        assert result.returncode == 0, result.stdout + result.stderr
        executed.append(set(re.findall(r"^PASSED (\S+)$", result.stdout, re.MULTILINE)))
    assert all(executed)
    assert set().union(*executed) == expected
    assert sum(len(shard) for shard in executed) == len(expected)


def test_invalid_test_shard_stops_before_collection(tmp_path: pathlib.Path) -> None:
    """範囲外の分割番号は、試験を実行せず指定方法を示す使用法エラーで終了する。"""
    (tmp_path / "probe_test.py").write_text("def test_probe():\n    pass\n", encoding="utf-8")
    result = _run_child_pytest(tmp_path, "--test-shard=4/3", "probe_test.py")
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "--test-shard=INDEX/COUNT" in result.stderr
    assert "passed" not in result.stdout


@pytest.mark.parametrize(
    ("markexpr", "name", "body", "expected"),
    [
        ("windows", "posix_only_test.py", "import os\n_UID = os.getuid()\n", True),
        ("windows", "boundary_test.py", "import pytest\n\npytestmark = pytest.mark.windows\n", False),
        ("windows", "helper.py", "import os\n", None),
        ("not browser and not live_agents", "posix_only_test.py", "import os\n_UID = os.getuid()\n", None),
    ],
    ids=["unmarked", "marked", "not-test-module", "other-expression"],
)
def test_windows_selection_skips_unmarked_test_modules(
    tmp_path: pathlib.Path, markexpr: str, name: str, body: str, expected: bool | None
) -> None:
    """`-m windows`では本文にmarkerが無い試験モジュールだけを読み込まず、他の選択では収集を変えない。"""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")

    class Config:
        def getoption(self, option: str) -> str:
            assert option == "markexpr"
            return markexpr

    assert pytest_plugin.pytest_ignore_collect(path, cast(pytest.Config, Config())) is expected


_SLOT_PROBE = """\
import json, os, pathlib, time
import pytest

@pytest.mark.parametrize("index", range(int(os.environ.get("SLOT_TESTS", "6"))))
def test_slow(index):
    evidence = pathlib.Path(os.environ["SLOT_EVIDENCE"])
    queued = pathlib.Path(os.environ["SLOT_QUEUED"])
    # 最初の試験の完了後に始まる試験は、待機者が実行枠の待ち列へ並んだ印を見てから進む。
    # 待機者の起動の速さに左右されず、残りの試験の区切りで待機者が並んでいる状態を作る。
    deadline = time.monotonic() + 60
    while list(evidence.glob("test-*.json")) and not queued.exists():
        assert time.monotonic() < deadline, "待機者が待ち列へ並ばない"
        time.sleep(0.02)
    # 印`SLOT_AFTER`の後に始まる試験は、長い待機者が待ち列へ並んだ印`SLOT_LONG_QUEUED`も待つ（指定した試験だけ）。
    after, long_queued = os.environ.get("SLOT_AFTER"), os.environ.get("SLOT_LONG_QUEUED")
    while after and long_queued and pathlib.Path(after).exists() and not pathlib.Path(long_queued).exists():
        assert time.monotonic() < deadline, "長い待機者が待ち列へ並ばない"
        time.sleep(0.02)
    started = time.time()
    time.sleep(0.4)
    pathlib.Path(os.environ["SLOT_EVIDENCE"], f"test-{index}.json").write_text(
        json.dumps({"started": started, "ended": time.time()}), encoding="utf-8"
    )
"""

_SLOT_WAITER = """\
import json, os, pathlib, sys, time
from pyfltr.command import execution_slot

# `on_wait`は待ち列への登録後に呼ばれるため、印は登録の完了を示す。印の保存先を渡さない試験では書かない。
queued = os.environ.get("SLOT_QUEUED")

def mark(_owner=None):
    if queued:
        pathlib.Path(queued).write_text("", encoding="utf-8")

with execution_slot.hold(label="waiter-probe", on_wait=mark):
    mark()
    started = time.time()
    time.sleep(float(os.environ.get("SLOT_HOLD", "0.3")))
    pathlib.Path(sys.argv[1]).write_text(json.dumps({"started": started, "ended": time.time()}), encoding="utf-8")
"""

_SLOT_OUTER = """\
import subprocess, sys
from pyfltr.command import execution_slot

with execution_slot.hold(label="outer-probe"):
    raise SystemExit(subprocess.run(sys.argv[1:], check=False).returncode)
"""


@pytest.mark.parametrize(("workers", "outer"), [(2, False), (0, False), (2, True)])
def test_pytest_yields_slot_between_tests_and_resumes_remaining(tmp_path: pathlib.Path, workers: int, outer: bool) -> None:
    """最外側の所有では試験の区切りで待機者へ譲って残りを再開し、継承時は外側の解放まで譲らない。

    待機者の保持区間がどの試験の実行区間とも重ならないこと（workerの稼働中の排他）と、全試験の結果を確かめる。
    残りの試験は待機者が待ち列へ並んだ印を待ってから進むため、待機者の起動が遅れても判定は変わらない。
    """
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (tmp_path / "probe_test.py").write_text(_SLOT_PROBE, encoding="utf-8")
    (tmp_path / "waiter.py").write_text(_SLOT_WAITER, encoding="utf-8")
    (tmp_path / "outer.py").write_text(_SLOT_OUTER, encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYFLTR_CACHE_DIR=str(tmp_path / "pyfltr"),
        SLOT_EVIDENCE=str(evidence),
        SLOT_QUEUED=str(tmp_path / "queued"),
    )
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "-p", pytest_plugin.__name__]
    argv += ["-p", "xdist.plugin", "-n", str(workers), str(tmp_path / "probe_test.py")]
    if outer:
        argv = [sys.executable, str(tmp_path / "outer.py"), *argv]
    waited = tmp_path / "waiter.json"
    with subprocess.Popen(
        argv, cwd=tmp_path, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
    ) as child:
        deadline = time.monotonic() + 60
        # 最初の試験の完了後に待機者を起動する。残りの試験は待機者が並んだ印を待ってから進む。
        while not list(evidence.glob("test-*.json")):
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        waiter = subprocess.run(
            [sys.executable, str(tmp_path / "waiter.py"), str(waited)], env=environment, timeout=60, check=False
        )
        output, _ = child.communicate(timeout=60)
    assert waiter.returncode == 0
    assert child.returncode == 0, output
    assert "6 passed" in output
    tests = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(evidence.glob("test-*.json"))]
    assert len(tests) == 6
    window = json.loads(waited.read_text(encoding="utf-8"))
    assert all(test["ended"] <= window["started"] or window["ended"] <= test["started"] for test in tests), (window, tests)
    last = max(test["ended"] for test in tests)
    # 最外側の所有では残りの試験より先に待機者が実行され、継承時はpytestの終了後まで待つ。
    assert (window["ended"] < last) is not outer, (window, tests)


_SLOT_FLOOD = """\
import os, pathlib, subprocess, sys

# 停止の印が置かれるまで、長く保持する待機者を次々に起動し、待ち列が空にならない状態を作る。
stop = pathlib.Path(sys.argv[1])
waiter, log_directory = sys.argv[2], pathlib.Path(sys.argv[3])
index = 0
while not stop.exists():
    subprocess.run([sys.executable, waiter, str(log_directory / f"{os.getpid()}-{index}.json")], check=True, timeout=60)
    index += 1
"""


@pytest.mark.parametrize(("workers", "budget"), [(2, 2.5), (0, 2.5), (2, 6.0)])
def test_pytest_stops_yielding_after_budget_while_long_waiters_keep_queueing(
    tmp_path: pathlib.Path, workers: int, budget: float
) -> None:
    """長い検証が待ち列へ次々に並んでも、譲渡の累計時間の上限の後は譲らずに残りの試験を完了する。

    短い待機者は従来どおり区切りで実行枠を譲られて先に終わり、長い待機者が並び続けてもpytestは有限の譲渡の後に終わることを、
    同じ実行の試験と待機者の実行区間の記録から確かめる。短い待機者の後に始まる試験は長い待機者が並んだ印を待つため、
    上限に達する前に、長い待機者が待ち列に並んだ状態が必ず生じる。上限6秒の対照では、同じ数え方で上限2.5秒の許容件数を超える。
    上限2.5秒は、xdistで短い待機者へ譲る所要（作業者の区切り待ちを含め1秒前後）の後にも長い待機者へ1回は譲れる長さにする。
    """
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    windows = tmp_path / "windows"
    windows.mkdir()
    stop = tmp_path / "stop"
    (tmp_path / "probe_test.py").write_text(_SLOT_PROBE, encoding="utf-8")
    (tmp_path / "waiter.py").write_text(_SLOT_WAITER, encoding="utf-8")
    (tmp_path / "flood.py").write_text(_SLOT_FLOOD, encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYFLTR_CACHE_DIR=str(tmp_path / "pyfltr"),
        SLOT_EVIDENCE=str(evidence),
        SLOT_QUEUED=str(tmp_path / "queued"),
        SLOT_TESTS="12",
        SLOT_AFTER=str(windows / "short.json"),
        SLOT_LONG_QUEUED=str(tmp_path / "long-queued"),
    )
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "-p", pytest_plugin.__name__]
    argv += ["-o", f"execution_slot_yield_budget={budget}", "-p", "xdist.plugin", "-n", str(workers)]
    argv += [str(tmp_path / "probe_test.py")]
    with contextlib.ExitStack() as stack:
        child = stack.enter_context(
            subprocess.Popen(
                argv,
                cwd=tmp_path,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )
        )
        deadline = time.monotonic() + 60
        while not list(evidence.glob("test-*.json")):
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        short = subprocess.run(
            [sys.executable, str(tmp_path / "waiter.py"), str(windows / "short.json")],
            env=environment,
            timeout=60,
            check=False,
        )
        assert short.returncode == 0
        long_environment = {**environment, "SLOT_HOLD": "1.5", "SLOT_QUEUED": str(tmp_path / "long-queued")}
        floods = [
            stack.enter_context(
                subprocess.Popen(
                    [sys.executable, str(tmp_path / "flood.py"), str(stop), str(tmp_path / "waiter.py"), str(windows)],
                    env=long_environment,
                )
            )
            for _ in range(2)
        ]
        try:
            output, _ = child.communicate(timeout=90)
        finally:
            stop.write_text("", encoding="utf-8")
            for flood in floods:
                flood.wait(timeout=60)
    assert child.returncode == 0, output
    assert "12 passed" in output
    tests = [json.loads(path.read_text(encoding="utf-8")) for path in evidence.glob("test-*.json")]
    last = max(test["ended"] for test in tests)
    short_window = json.loads((windows / "short.json").read_text(encoding="utf-8"))
    # 短い待機者は、残りの試験の区切りで実行枠を譲られて先に終わる。
    assert short_window["ended"] < last, (short_window, tests)
    long_windows = [json.loads(path.read_text(encoding="utf-8")) for path in windows.glob("*-*.json")]
    yielded = len([window for window in long_windows if window["started"] < last])
    if budget < 6:
        # 長い待機者（1件1.5秒）へ1回は譲り、並び続けても上限（2.5秒）を超えるまでの高々2件で譲るのをやめる。
        assert 1 <= yielded <= 2, (long_windows, tests)
    else:
        # 上限を広げると同じ条件で3件以上へ譲る。上限2.5秒の件数の制限が上限の効果であることの対照である。
        assert yielded >= 3, (long_windows, tests)


_SLOT_PARTICIPANT = """\
import pathlib, sys, time
from pyfltr.command import execution_slot

ticket, mode, done = sys.argv[1:4]
with execution_slot.hold(participant=ticket):
    if mode == "boundary":
        while not execution_slot.yield_requested():
            time.sleep(0.05)
        print("yielded", execution_slot.yield_to_waiters(), flush=True)
pathlib.Path(done).write_text("", encoding="utf-8")
"""

_SLOT_DETACHED_WAITER = """\
import subprocess, sys

# 孫を起動して親だけ終了し、待機者を協調所有者の子孫から外す（子孫は所有を継承して待たないため）。
subprocess.Popen([sys.executable, *sys.argv[1:]], start_new_session=True)
"""


def test_cooperative_owner_yields_after_finished_worker_released(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """先に終了したworkerの参加区間を完了印の後に閉じ、区切りで止まった残りのworkerの間に待機者へ譲る。

    終了した参加者の登録を残すと協調所有者は譲らず、止まったworkerが再開されないままpytestが終了しない。
    """
    monkeypatch.setenv("PYFLTR_CACHE_DIR", str(tmp_path / "pyfltr"))
    (tmp_path / "participant.py").write_text(_SLOT_PARTICIPANT, encoding="utf-8")
    (tmp_path / "detach.py").write_text(_SLOT_DETACHED_WAITER, encoding="utf-8")
    (tmp_path / "waiter.py").write_text(_SLOT_WAITER, encoding="utf-8")
    owner = slot_yield.CooperativeOwner(lambda _owner: None)
    owner.start()
    try:
        assert not owner.inherited
        distribution = slot_yield.TicketDistribution(owner)
        nodes = [SimpleNamespace(gateway=SimpleNamespace(id=f"gw{index}"), workerinput={}) for index in range(2)]
        for node in nodes:
            distribution.pytest_configure_node(node)
        tickets = [node.workerinput[slot_yield.TICKET_KEY] for node in nodes]
        dones = [pathlib.Path(node.workerinput[slot_yield.DONE_KEY]) for node in nodes]
        finished = subprocess.run(
            [sys.executable, str(tmp_path / "participant.py"), tickets[0], "finish", str(dones[0])], timeout=30, check=False
        )
        assert finished.returncode == 0
        distribution.pytest_testnodedown(nodes[0], None)
        with subprocess.Popen(
            [sys.executable, str(tmp_path / "participant.py"), tickets[1], "boundary", str(dones[1])],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as boundary:
            waited = tmp_path / "waiter.json"
            subprocess.run(
                [sys.executable, str(tmp_path / "detach.py"), str(tmp_path / "waiter.py"), str(waited)],
                timeout=30,
                check=True,
            )
            try:
                output, _ = boundary.communicate(timeout=30)
            finally:
                if boundary.poll() is None:
                    boundary.kill()
        assert boundary.returncode == 0
        assert "yielded True" in output
        assert waited.exists()
    finally:
        owner.close()
    assert not owner.done_directory.exists()
