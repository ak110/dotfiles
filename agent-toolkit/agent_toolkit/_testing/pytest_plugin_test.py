"""実pytestの収集を通し、自動fixtureの登録とディレクトリ境界を確かめる。"""

import contextlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import textwrap

import psutil
import pytest

from agent_toolkit._testing import pytest_plugin


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
    assert not psutil.pid_exists(observed["pid"]) or psutil.Process(observed["pid"]).status() == psutil.STATUS_ZOMBIE


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
            from agent_toolkit._atk.managed_temp import registry

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
                assert registry._temp_root() == tmp_path / "managed-temp"
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
    # 回収済みの子は照会の間に消えることがあるため、消滅とzombieの両方を回収済みとみなす。
    with contextlib.suppress(psutil.NoSuchProcess):
        assert psutil.Process(observed["child"]).status() == psutil.STATUS_ZOMBIE


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
