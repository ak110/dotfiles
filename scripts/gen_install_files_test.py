"""scripts/gen_install_files.py のテスト。

公開インターフェース`main()`経由で、実リポジトリの同期状態、模擬rules追加時の再生成、
マーカー欠落時のエラーを検証する。
"""

import dataclasses
import pathlib
import types

import gen_install_files
import pytest
from _scripts_test_helpers import (
    INSTALL_FILES_BEGIN,
    INSTALL_FILES_END,
    expected_ps1_block,
    expected_sh_block,
    extract_install_files_block,
)


@dataclasses.dataclass
class _Env:
    """テスト用の疑似リポジトリパス一式と対象モジュール。"""

    module: types.ModuleType
    rules_dir: pathlib.Path
    install_sh: pathlib.Path
    install_ps1: pathlib.Path


@pytest.fixture
def _env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> _Env:
    """疑似リポジトリ構造をtmp_path配下に用意し、モジュール定数を差し替えて返す。"""
    module = gen_install_files
    rules_dir = tmp_path / "agent-toolkit" / "rules"
    install_sh = tmp_path / "install-claude.sh"
    install_ps1 = tmp_path / "install-claude.ps1"
    rules_dir.mkdir(parents=True)
    install_sh.write_text(_script_with_block("FILES=(\n)\n"), encoding="utf-8")
    install_ps1.write_bytes(b"\xef\xbb\xbf" + _script_with_block("$files = @(\r\n)\r\n").encode("utf-8"))
    monkeypatch.setattr(module, "_RULES_DIR", rules_dir)
    monkeypatch.setattr(module, "_INSTALL_SH", install_sh)
    monkeypatch.setattr(module, "_INSTALL_PS1", install_ps1)
    return _Env(module=module, rules_dir=rules_dir, install_sh=install_sh, install_ps1=install_ps1)


def test_regenerate_with_added_rule(_env: _Env) -> None:
    """模擬rules配下のmdファイル一覧が両install scriptのブロックへ反映される。"""
    (_env.rules_dir / "02-collaboration.md").write_text("", encoding="utf-8")
    (_env.rules_dir / "01-agent.md").write_text("", encoding="utf-8")

    assert _env.module.main([]) == 0

    assert extract_install_files_block(_env.install_sh.read_text(encoding="utf-8")) == expected_sh_block(
        ["01-agent.md", "02-collaboration.md"]
    )
    ps1_bytes = _env.install_ps1.read_bytes()
    assert ps1_bytes.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in ps1_bytes
    assert extract_install_files_block(ps1_bytes.decode("utf-8-sig")) == expected_ps1_block(
        ["01-agent.md", "02-collaboration.md"]
    )


def test_missing_marker_raises(_env: _Env) -> None:
    """マーカーが欠落している場合はSystemExitで失敗する。"""
    _env.install_sh.write_text("FILES=()\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="marker not found"):
        _env.module.main([])


def _script_with_block(block: str) -> str:
    return f"before\n# {INSTALL_FILES_BEGIN}\n{block}# {INSTALL_FILES_END}\nafter\n"
