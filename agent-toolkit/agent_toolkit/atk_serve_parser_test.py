"""atk serve parserの契約を検証する。"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import types

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.atk_support import _isolate_agent_and_managed_temp_environment

__all__ = ["_isolate_agent_and_managed_temp_environment"]


class TestServeParser:
    """serveトップレベルサブコマンドの引数境界を検証する。"""

    def test_defaults_and_explicit_values(self) -> None:
        """host/portの省略値と明示値を保持する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        defaults = parser.parse_args(["serve"])
        explicit = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "65535"])
        assert (defaults.host, defaults.port) == (None, None)
        assert (explicit.host, explicit.port) == ("0.0.0.0", 65535)

    @pytest.mark.parametrize("port", ["0", "65536"])
    def test_rejects_out_of_range_port(self, port: str) -> None:
        """port範囲外をargparseエラーにする。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as error:
            parser.parse_args(["serve", "--port", port])
        assert error.value.code == 2

    def test_dispatches_resolved_arguments(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """mainがhost・port・homeをserve起動層へ渡して正常終了する。"""
        calls: list[dict[str, object]] = []

        def run(*, host: str | None, port: int | None, home: pathlib.Path) -> None:
            calls.append({"host": host, "port": port, "home": home})

        serve = types.ModuleType("agent_toolkit._atk.serve.cli")
        serve.__dict__["run"] = run
        monkeypatch.setitem(sys.modules, "agent_toolkit._atk.serve.cli", serve)
        with pytest.raises(SystemExit) as error:
            atk.main(["serve", "--host", "127.0.0.2", "--port", "28766"], home=tmp_path)
        assert error.value.code == 0
        assert calls == [{"host": "127.0.0.2", "port": 28766, "home": tmp_path}]

    def test_non_serve_import_does_not_load_serve_dependencies(self) -> None:
        """fresh processでatkを読み込んでもserve実装を解決しない。"""
        script_dir = pathlib.Path(atk.__file__).resolve().parent
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(script_dir)!r}); "
            "import atk; "
            "raise SystemExit(1 if 'agent_toolkit._atk.serve.cli' in sys.modules else 0)"
        )

        result = subprocess.run([sys.executable, "-c", code], check=False)

        assert result.returncode == 0
