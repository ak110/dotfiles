"""テスト選択の提出物を、必須テストの漏れ・余剰・未知の識別子に分けて採点する。

期待集合は公開素材の利用側の実コードから導いた。
- `change-shared-fixture`: `spec_support.load_sample_config`を使うテスト（`settings_spec.py`、`loader_spec.py`）
- `change-cli-key`: `report`をimportするテストと、`app_cli.py`を子プロセスで実行して項目名を完全一致で比べるテスト
- `change-plugin-contract`: `plugin_csv`をimportするテスト、`runner.py`経由の子プロセス実行、`loader.py`経由の動的import
- `change-protocol`: `registry.resolve`へ名前を渡す経路が設定・環境変数・引数のどれからでもあり、利用側を確定できないため全件
- `change-local-util`: `util_text`をimportするテストだけ
"""

import pathlib
import runpy
import sys

ALL = {
    "report_spec.py::test_build_report_values",
    "cli_output_spec.py::test_cli_output_keys",
    "settings_spec.py::test_timeout_default",
    "loader_spec.py::test_loads_configured_plugins",
    "plugin_csv_spec.py::test_parse_cells",
    "runner_spec.py::test_runner_invokes_plugin",
    "util_text_spec.py::test_slugify",
    "protocol_spec.py::test_encode_sorted_pairs",
}
EXPECTED = {
    "change-shared-fixture": {"settings_spec.py::test_timeout_default", "loader_spec.py::test_loads_configured_plugins"},
    "change-cli-key": {"report_spec.py::test_build_report_values", "cli_output_spec.py::test_cli_output_keys"},
    "change-plugin-contract": {
        "plugin_csv_spec.py::test_parse_cells",
        "runner_spec.py::test_runner_invokes_plugin",
        "loader_spec.py::test_loads_configured_plugins",
    },
    "change-protocol": ALL,
    "change-local-util": {"util_text_spec.py::test_slugify"},
}


def _checks():
    for change_id, required in EXPECTED.items():
        yield f"{change_id}-required", lambda task, c=change_id, s=required: s <= set(task.select(c)), True
        # 全件が期待の場面では余剰が生じないため、余剰の検査を置かない。
        if required != ALL:
            yield f"{change_id}-no-extra", lambda task, c=change_id, s=required: not (set(task.select(c)) & ALL) - s, True
        yield f"{change_id}-known", lambda task, c=change_id: set(task.select(c)) <= ALL, True


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](sys.argv[1], list(_checks()))
