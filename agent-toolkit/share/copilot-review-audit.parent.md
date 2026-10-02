# 自動コードレビュー監査担当の起動と受領

```text
起動対象: copilot-review-audit.subagent.md
```

`agent-toolkit:process-wi`のメインが自動コードレビュー監査を委譲するときに、本書に従って監査担当を起動し、結果を受領する。
監査の手順と返却項目は`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`が定める。

## 起動

`copilot-review-audit.subagent.md`を指定する起動（`agent-toolkit:delegation`の`references/base-contract.md`「`<役割名>.subagent.md`を指定する起動」）で1件の監査担当を起動する。`cwd`は対象リポジトリの絶対パスとし、`extra_params`には次の名前付き入力だけを渡す。

- `pending取得結果`: `atk review-audit pending`の標準出力をセッションのmanaged-temp（`agent-toolkit:managed-temp`）へ保存したJSONファイルの絶対パス。JSONはCopilot由来の`reviews`・`threads`とDependabotアラートの`dependabot`を持つ。コマンドが非0で終わった場合と、JSONまたは件数を解釈できない場合は`なし`
- `引き継ぎ記録先`: 値は`agent-toolkit:delegation`の`references/base-contract.md`「`<役割名>.subagent.md`を指定する起動」が指す`引き継ぎ記録先`の書式に従う

監査の手順、返却項目、権限および応答言語は`copilot-review-audit.subagent.md`が定めるため、委譲プロンプトで再掲しない。

## 受領

結果は引数なしの`atk agents wait`で受け取る。返却が`copilot-review-audit.subagent.md`の定める項目を持つことを確かめ、要修正の指摘と要修正のDependabotアラートを同じセッションで是正するかAWIへ記録するかを確定する。Dependabotアラートを取得できなかった状態（機能無効または権限不足）は完了報告へ渡す。返却の受領と処置の確定を公開工程の開始条件とする。
