"""登録された公開スクリプトと配布原本の整合を検証する。"""

from agent_toolkit._atk import run_script


def test_registry_stays_inside_plugin_root() -> None:
    for relative in run_script.SCRIPT_PATHS.values():
        target = (run_script.PLUGIN_ROOT / relative).resolve()
        assert target.is_relative_to(run_script.PLUGIN_ROOT)
        assert target.is_file()
