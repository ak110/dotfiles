# 自動コードレビュー監査担当の起動と受領

```text
起動対象: copilot-review-audit.subagent.md
```

`agent-toolkit:process-wi`のメインが自動コードレビュー監査を委譲するときに、本書に従って監査担当を起動し、結果を受領する。
監査の手順と返却項目は`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`が定める。

## 起動

1件の監査担当を起動する。

`agents_server`の`start`を次の引数で呼ぶ。起動の定型と適用する義務は`agent-toolkit:delegation`の「`<役割名>.parent.md`を持つ委譲の起動」に従う。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象リポジトリの絶対パス |
| `subagent_md_path` | `copilot-review-audit` |
| `extra_params` | 次の名前付き入力だけ |
| `mode` | 指定しない |
| `model_type` | 指定しない（サーバーが工程別設定を使う） |

- `pending取得結果`: `atk review-audit pending`の標準出力のJSONを持つファイルの絶対パス。`atk`が長い標準出力を保存して`保存先:`を示した場合はその絶対パスを渡し、直接表示された場合は表示されたJSONをセッションのmanaged-temp（`agent-toolkit:managed-temp`）のファイルへ書いて渡す。`atk`の出力はリダイレクトで保存しない（`agent-toolkit/rules/02-agent-operations.md`の`atk`の項）。JSONはCopilot由来の`reviews`・`threads`とDependabotアラートの`dependabot`を持つ。コマンドが非0で終わった場合と、JSONまたは件数を解釈できない場合は`なし`
- `引き継ぎ記録先`: `（新規）`は省略してサーバーに用意させる。継続の扱いは`agent-toolkit:delegation`の「`<役割名>.parent.md`を持つ委譲の起動」に従う

## 受領

結果は引数なしの`atk agents wait`で受け取る。返却が`copilot-review-audit.subagent.md`の定める項目を持つことを確かめ、要修正の指摘と要修正のDependabotアラートを同じセッションで是正するかAWIへ記録するかを確定する。Dependabotアラートを取得できなかった状態（機能無効または権限不足）は完了報告へ渡す。
