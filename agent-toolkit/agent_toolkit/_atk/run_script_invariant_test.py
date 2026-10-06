"""登録された公開スクリプトと配布原本の整合を検証する。"""

import argparse
import pathlib

import pytest

from agent_toolkit._atk import run_script


class _ParserCaptured(Exception):
    """登録スクリプトが組み立てた引数解析器を、解析の直前で取り出すための例外。"""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__(parser.prog)
        self.parser = parser


def test_registry_stays_inside_plugin_root() -> None:
    for relative in run_script.SCRIPT_PATHS.values():
        target = (run_script.PLUGIN_ROOT / relative).resolve()
        assert target.is_relative_to(run_script.PLUGIN_ROOT)
        assert target.is_file()


def _registered_parser(monkeypatch: pytest.MonkeyPatch, script_name: str) -> argparse.ArgumentParser:
    """`atk run-script`と同じ起動経路でスクリプトを実行し、最初に解析を始めた解析器を返す。"""

    def _capture(self: argparse.ArgumentParser, *args: object, **kwargs: object) -> argparse.Namespace:
        del args, kwargs
        raise _ParserCaptured(self)

    with monkeypatch.context() as patch:
        patch.setattr(argparse.ArgumentParser, "parse_args", _capture)
        with pytest.raises(_ParserCaptured) as captured:
            run_script.dispatch(argparse.Namespace(script_name=script_name, script_args=[]))
    return captured.value.parser


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
