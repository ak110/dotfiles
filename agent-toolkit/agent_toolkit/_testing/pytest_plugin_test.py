"""実pytestの収集を通し、自動fixtureの登録とディレクトリ境界を確かめる。"""

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest


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
