# 変更ごとのテスト選択

このディレクトリは小さな模擬リポジトリである。`*.py`がコード、`*_spec.py`がテスト、`spec_support.py`がテストの共有処理、`settings.toml`が設定、`change-*.diff`が検討中の変更である。
`task.py`の`select`を修正し、変更IDごとに、その変更の後で実行すべきテスト識別子を返す。

- テスト識別子は`<ファイル名>::<関数名>`の形で書く（例: `report_spec.py::test_build_report_values`）。
- 変更が影響するテストを漏らさず、影響しないテストは選ばない。
- 利用側を確定できない変更では、使用できるテスト識別子の全件を選ぶ。
- 下表に無いテスト識別子は使わない。

追加の質問は不要であり、公開関数の引数と戻り値の形式を維持して提出する。

| 変更ID | 差分 |
| --- | --- |
| `change-shared-fixture` | `change-shared-fixture.diff` |
| `change-cli-key` | `change-cli-key.diff` |
| `change-plugin-contract` | `change-plugin-contract.diff` |
| `change-protocol` | `change-protocol.diff` |
| `change-local-util` | `change-local-util.diff` |

使用できるテスト識別子:

- `report_spec.py::test_build_report_values`
- `cli_output_spec.py::test_cli_output_keys`
- `settings_spec.py::test_timeout_default`
- `loader_spec.py::test_loads_configured_plugins`
- `plugin_csv_spec.py::test_parse_cells`
- `runner_spec.py::test_runner_invokes_plugin`
- `util_text_spec.py::test_slugify`
- `protocol_spec.py::test_encode_sorted_pairs`
