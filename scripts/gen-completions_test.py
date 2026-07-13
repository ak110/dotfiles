"""scripts/gen-completions.py のテスト。

ファイル名にハイフンを含み通常のimport文で読み込めないため、`importlib`経由でロードする。
公開インターフェース`main()`経由でファイル走査条件・ラッパー実在判定・
2出力先（`completions/_pytools.bash`・`agent-toolkit/completions/atk.bash`）への
分岐書き込みを検証する。
"""

import dataclasses
import importlib.util
import pathlib
import types

import pytest

_MODULE_PATH = pathlib.Path(__file__).resolve().parent / "gen-completions.py"


def _load_module() -> types.ModuleType:
    """ハイフン付きファイル名のスクリプトを独立モジュールとしてロードする。"""
    spec = importlib.util.spec_from_file_location("gen_completions", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclasses.dataclass
class _Env:
    """テスト用の疑似リポジトリパス一式と対象モジュール。"""

    module: types.ModuleType
    repo_root: pathlib.Path
    pyproject: pathlib.Path
    pytools_output: pathlib.Path
    atk_output: pathlib.Path
    scripts_dir: pathlib.Path
    bin_dir: pathlib.Path


@pytest.fixture
def _env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> _Env:
    """疑似リポジトリ構造をtmp_path配下に用意し、モジュール定数を差し替えて返す。"""
    module = _load_module()
    pyproject = tmp_path / "pyproject.toml"
    pytools_output = tmp_path / "completions" / "_pytools.bash"
    atk_output = tmp_path / "agent-toolkit" / "completions" / "atk.bash"
    scripts_dir = tmp_path / "agent-toolkit" / "scripts"
    bin_dir = tmp_path / "agent-toolkit" / "bin"
    scripts_dir.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    pytools_output.parent.mkdir()
    atk_output.parent.mkdir()
    pyproject.write_text('[project]\nname = "x"\n', encoding="utf-8")
    monkeypatch.setattr(module, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "_PYPROJECT", pyproject)
    monkeypatch.setattr(module, "_PYTOOLS_OUTPUT", pytools_output)
    monkeypatch.setattr(module, "_ATK_OUTPUT", atk_output)
    monkeypatch.setattr(module, "_AGENT_TOOLKIT_SCRIPTS", scripts_dir)
    monkeypatch.setattr(module, "_AGENT_TOOLKIT_BIN", bin_dir)
    return _Env(
        module=module,
        repo_root=tmp_path,
        pyproject=pyproject,
        pytools_output=pytools_output,
        atk_output=atk_output,
        scripts_dir=scripts_dir,
        bin_dir=bin_dir,
    )


class TestMainFileScanConditions:
    """`main()`経由での`agent-toolkit/scripts/*.py`走査条件を検証する。"""

    def test_marker_and_wrapper_present_is_included(self, _env: _Env) -> None:
        """argcompleteマーカーを持ちbashラッパーが存在するスクリプトは補完対象へ含まれる。"""
        (_env.scripts_dir / "foo.py").write_text("# PYTHON_ARGCOMPLETE_OK\n", encoding="utf-8")
        (_env.bin_dir / "foo").write_text("#!/bin/sh\n", encoding="utf-8")
        assert _env.module.main([]) == 0
        content = _env.atk_output.read_text(encoding="utf-8")
        assert "complete -o nospace -o default -o bashdefault -F _python_argcomplete foo" in content

    def test_underscore_prefixed_script_is_excluded(self, _env: _Env) -> None:
        """アンダースコア始まりのスクリプトはマーカー・ラッパーを持っていても走査対象から除外される。"""
        (_env.scripts_dir / "_internal.py").write_text("# PYTHON_ARGCOMPLETE_OK\n", encoding="utf-8")
        (_env.bin_dir / "_internal").write_text("#!/bin/sh\n", encoding="utf-8")
        _env.module.main([])
        content = _env.atk_output.read_text(encoding="utf-8")
        assert "_internal" not in content

    def test_script_without_marker_is_excluded(self, _env: _Env) -> None:
        """argcompleteマーカーを持たないスクリプトは補完対象から除外される。"""
        (_env.scripts_dir / "bar.py").write_text("print('no marker')\n", encoding="utf-8")
        (_env.bin_dir / "bar").write_text("#!/bin/sh\n", encoding="utf-8")
        _env.module.main([])
        content = _env.atk_output.read_text(encoding="utf-8")
        assert "bar" not in content

    def test_script_without_wrapper_is_excluded(self, _env: _Env) -> None:
        """対応するbashラッパーが`agent-toolkit/bin/`配下に存在しないスクリプトは補完対象から除外される。"""
        (_env.scripts_dir / "baz.py").write_text("# PYTHON_ARGCOMPLETE_OK\n", encoding="utf-8")
        _env.module.main([])
        content = _env.atk_output.read_text(encoding="utf-8")
        assert "baz" not in content


class TestMainOutputRouting:
    """`main()`経由での2出力先への分岐書き込みと再書き込み抑制を検証する。"""

    def test_pytools_and_agent_toolkit_commands_route_to_separate_outputs(self, _env: _Env) -> None:
        """`[project.scripts]`由来のコマンドは`_pytools.bash`へ、`agent-toolkit/scripts/`由来は`atk.bash`へ書き込まれる。"""
        _env.pyproject.write_text(
            '[project]\nname = "x"\n\n[project.scripts]\nmytool = "mypkg.mytool:main"\n',
            encoding="utf-8",
        )
        pkg = _env.repo_root / "mypkg"
        pkg.mkdir()
        (pkg / "mytool.py").write_text("# PYTHON_ARGCOMPLETE_OK\n", encoding="utf-8")
        (_env.scripts_dir / "atktool.py").write_text("# PYTHON_ARGCOMPLETE_OK\n", encoding="utf-8")
        (_env.bin_dir / "atktool").write_text("#!/bin/sh\n", encoding="utf-8")

        assert _env.module.main([]) == 0

        pytools_content = _env.pytools_output.read_text(encoding="utf-8")
        atk_content = _env.atk_output.read_text(encoding="utf-8")
        assert "mytool" in pytools_content
        assert "mytool" not in atk_content
        assert "atktool" in atk_content
        assert "atktool" not in pytools_content

    def test_no_rewrite_when_content_unchanged(self, _env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
        """出力先の内容が変わらない再実行では書き換えず生成メッセージも出力しない。"""
        assert _env.module.main([]) == 0
        capsys.readouterr()
        mtime_before = _env.atk_output.stat().st_mtime_ns

        assert _env.module.main([]) == 0

        out = capsys.readouterr().out
        assert "生成:" not in out
        assert _env.atk_output.stat().st_mtime_ns == mtime_before
