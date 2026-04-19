# codex CLIによる計画ファイルレビュー

MCPツール（`mcp__codex__codex` / `mcp__codex__codex-reply`）が利用できない場合のフォールバック手順。
本ファイルはCLI実行が必要になった時だけ参照する。

## 初回実行

`--cd`でプロジェクトディレクトリを指定し、計画ファイルのパスを引数に与える。
`--output-last-message`でレビュー結果をファイルに書き出す。
codexの出力（session id含む）はstderrに出るため、`2>&1 | grep` でsession id行のみClaudeへ返す。
`set -o pipefail` によりcodexが失敗した場合はその終了コードがそのままシェルに返る。

プロンプト文言（`{初回プロンプト}`）はSKILL.mdの「MCPツール版（優先）」の初回`prompt`の内容。

```bash
set -o pipefail && codex exec --dangerously-bypass-approvals-and-sandbox --cd "{project_directory}" --output-last-message "{plan_full_path}.review.md" \
  "{初回プロンプト}" \
  2>&1 | grep "^session id:"
```

session idの抽出に失敗した場合やcodexがエラー終了した場合は、grepを外して `codex exec ... 2>&1` で再実行し全文を確認する。

## 2回目以降

`exec resume`を使用して前回のレビューから続行する。
`SESSION_ID` は初回コマンドの `grep "^session id:"` で抽出された `session id:` 行から取得する。
`{plan_full_path}.review.md` には含まれない。
注意: `--last` は並列セッション実行時に意図しないセッションを再開する恐れがあるため使用しない。

プロンプト文言（`{再レビュープロンプト}`）はSKILL.mdの「MCPツール版（優先）」の2回目以降`prompt`の内容。

```bash
codex exec resume --dangerously-bypass-approvals-and-sandbox --output-last-message "{plan_full_path}.review.md" {SESSION_ID} \
  "{再レビュープロンプト}"
```

## レビュー結果の読み取り

レビュー結果は `{plan_full_path}.review.md` に出力されるのでReadツールで読み取る。

## Windows環境での注意

codexはPowerShell経由でファイルを読み書きする際、デフォルトでShift-JISが使われて日本語が文字化けする。
対処として、codexへのプロンプトに以下を追記する。
「ファイルの読み書きはUTF-8エンコーディングを明示すること（例: `Get-Content -Encoding UTF8`）」
