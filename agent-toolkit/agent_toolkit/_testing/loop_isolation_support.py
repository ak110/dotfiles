"""イベントループとテスト単位の環境隔離を公開pytestの対照実行で確かめる。"""

import os
import pathlib
import subprocess
import sys
import textwrap


def assert_loop_scope_isolation(
    tmp_path: pathlib.Path, *, configuration: pathlib.Path, scope: str | None = None
) -> subprocess.CompletedProcess[str]:
    """両起動設定でfunctionは成功し、sessionへ戻す対照では前テストの処理が別環境へ波及する。"""
    probe = tmp_path / "loop_scope_test.py"
    probe.write_text(
        textwrap.dedent("""\
            import asyncio, os
            import pytest

            gate = None
            task = None
            observed = []

            @pytest.fixture(autouse=True)
            def environment(monkeypatch, tmp_path):
                monkeypatch.setenv('AGENT_TOOLKIT_PRIVATE_NOTES', str(tmp_path))

            @pytest.mark.asyncio
            async def test_first():
                global gate, task
                gate = asyncio.Event()
                original = os.environ['AGENT_TOOLKIT_PRIVATE_NOTES']
                async def background():
                    await gate.wait()
                    observed.append((original, os.environ['AGENT_TOOLKIT_PRIVATE_NOTES']))
                task = asyncio.create_task(background())
                await asyncio.sleep(0)

            @pytest.mark.asyncio
            async def test_second():
                gate.set()
                await asyncio.sleep(0)
                assert not observed, f'別テストの環境で前の処理が動いた: {observed}'
                assert task.done()
        """),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(configuration),
            "-o",
            "addopts=",
            *(
                []
                if scope is None
                else ["-o", f"asyncio_default_fixture_loop_scope={scope}", "-o", f"asyncio_default_test_loop_scope={scope}"]
            ),
            "-p",
            "pytest_asyncio.plugin",
            "-p",
            "no:cacheprovider",
            "--confcutdir",
            str(tmp_path),
            "-q",
            str(probe),
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == (1 if scope == "session" else 0), result.stdout + result.stderr
    if scope == "session":
        assert "別テストの環境で前の処理が動いた" in result.stdout
    else:
        assert "2 passed" in result.stdout

    return result
