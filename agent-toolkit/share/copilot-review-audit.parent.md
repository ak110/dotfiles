# 自動コードレビュー監査担当の起動と受領

```text
起動対象: copilot-review-audit.subagent.md
```

`agent-toolkit:process-wi`のメインが自動コードレビュー監査を委譲するときに、本書に従って監査担当を起動し、結果を受領する。
監査の手順と返却項目は`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`が定める。

## 起動

`agents_server`の`start`を次の引数で呼び、監査担当を1件起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象リポジトリの絶対パス |
| `subagent_md_path` | `copilot-review-audit` |
| `extra_params` | 次の名前付き入力だけ |

- `pending取得結果`: `atk review-audit pending`の標準出力のJSONを持つファイルの絶対パス。`atk`が長い標準出力を保存して`保存先:`を示した場合はその絶対パスを渡し、直接表示された場合は表示されたJSONをセッションのmanaged-temp（`agent-toolkit:managed-temp`）のファイルへ書いて渡す。JSONはCopilot由来の`reviews`・`threads`とDependabotアラートの`dependabot`を持つ。コマンドが非0で終わった場合と、JSONまたは件数を解釈できない場合は`なし`
- `引き継ぎ記録先`: 省略と継続の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

## 受領

結果は引数なしの`atk agents wait`で受け取る。返却が`copilot-review-audit.subagent.md`の`## 出力`の固定形式を満たし、`続行できない理由:`の行が無いことを確かめ、要修正の指摘と要修正のDependabotアラートを同じセッションで是正するかAWIへ記録するかを確定する。Dependabotアラートを取得できなかった状態（機能無効または権限不足）は完了報告へ渡す。
