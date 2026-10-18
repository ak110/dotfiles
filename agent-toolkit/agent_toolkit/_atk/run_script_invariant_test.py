"""登録された公開スクリプトと配布原本の整合を検証する。"""

import argparse
import pathlib

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._testing import cli_examples


def test_registry_stays_inside_plugin_root() -> None:
    for relative in run_script.SCRIPT_PATHS.values():
        target = (run_script.PLUGIN_ROOT / relative).resolve()
        assert target.is_relative_to(run_script.PLUGIN_ROOT)
        assert target.is_file()


def _registered_parser(monkeypatch: pytest.MonkeyPatch, script_name: str) -> argparse.ArgumentParser:
    """`atk run-script`と同じ起動経路でスクリプトを実行し、最初に解析を始めた解析器を返す。"""
    del monkeypatch
    return cli_examples.registered_parser(script_name)


def test_public_documents_examples_use_real_parsers() -> None:
    """配布原本と利用ガイドの完全な実行例を、登録先の実parserが受理するか確かめる。"""
    roots = [run_script.PLUGIN_ROOT / name for name in ("skills", "share", "rules")]
    guide = run_script.PLUGIN_ROOT.parent / "docs" / "guide"
    if guide.is_dir():
        roots.append(guide)
    parsers: dict[str, argparse.ArgumentParser] = {}
    checked = 0
    for root in roots:
        for path in sorted(root.rglob("*.md")):
            checked += cli_examples.validate_document(path, parsers=parsers)
    assert checked > 20


def test_every_registered_script_argument_states_its_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """全登録スクリプトの全引数が説明を持ち、パスを受け取る引数は使い方の行でパスと分かる。

    説明の無い引数や、使い方の行が引数名の大文字だけを示すパス型の引数は、値がファイルのパスか内容かを
    ヘルプから判断できず、JSON文字列をそのまま渡すなどの誤った呼び出しを招く。
    """
    checked: list[str] = []
    for script_name in sorted(run_script.SCRIPT_PATHS):
        parser = _registered_parser(monkeypatch, script_name)
        for action in parser._actions:  # pylint: disable=protected-access
            if isinstance(action, argparse._HelpAction):  # pylint: disable=protected-access
                continue
            assert action.help, (script_name, action.dest)
            if action.type is pathlib.Path:
                assert action.metavar in {"PATH", "DIR"}, (script_name, action.dest, action.metavar)
        checked.append(script_name)
    # 解析器を取り出せないスクリプトが残ったまま成功しないよう、確かめたスクリプトを登録の全件と比べる。
    assert checked == sorted(run_script.SCRIPT_PATHS)
