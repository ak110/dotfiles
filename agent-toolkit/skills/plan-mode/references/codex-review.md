# 計画ファイルのcodexレビュー

`mcp__codex__codex` / `mcp__codex__codex-reply` が利用可能ならMCPツール版を優先する。
利用不可ならCLIフォールバック版を使う。

## プロンプト

初回プロンプトは以下のテンプレートで構成する（MCP・CLI共通）。
`{review_standards_body}`には`agent-toolkit:review-standards`のSKILL.mdのH1見出し以降を埋め込む。

```text
{plan_full_path} この計画ファイルをレビューして。

レビュー観点:

- 発見した指摘は重大度ラベルを付けて全件報告する（採否の選別はメイン側で行う）
- `agent-toolkit:*`スキルや`plan-*-reviewer`などの識別子はClaude Code側の仕組みでcodex側からは存在確認できないため、これらの実在性に関する指摘は不要
- 計画ファイル自体は実装着手後に廃棄される一時的な作業文書であり、永続的なプロジェクト成果物ではない
  通常のコード・ドキュメントレビューでは指摘すべき以下の観点も、計画ファイルでは指摘対象外とする
  - 記述スタイル（章構成・段落構成・表現選択・書き方）への指摘。記述間の矛盾は対象に含む
  - 実装時にエージェントが判断可能な細部（変数名・エラーメッセージ文言・小規模なループ構造・局所的な制御フローなど）への指摘

{review_standards_body}
```

2回目以降は`計画ファイルを更新したので再レビューして。`を渡す。

## MCPツール版

- 初回: `mcp__codex__codex`（`cwd`: `"{project_directory}"`、`sandbox`: `"danger-full-access"`、`prompt`: 初回プロンプト）
- 2回目以降: `mcp__codex__codex-reply`（`threadId`: 前回の戻り値、`prompt`: 再レビュープロンプト）

## CLIフォールバック版

初回:

```bash
set -o pipefail && codex exec --dangerously-bypass-approvals-and-sandbox --cd "{project_directory}" --output-last-message "{plan_full_path}.review.md" \
  "{初回プロンプト}" \
  2>&1 | grep "^session id:"
```

session idの抽出に失敗、またはcodexがエラー終了した場合は、grepを外して`codex exec ... 2>&1`で再実行し全文を確認する。

2回目以降:

```bash
codex exec resume --dangerously-bypass-approvals-and-sandbox --output-last-message "{plan_full_path}.review.md" {SESSION_ID} \
  "{再レビュープロンプト}"
```

`SESSION_ID`は初回コマンドの`grep "^session id:"`で抽出する。
`--last`は並列セッション実行時に意図しないセッションを再開する恐れがあるため使用しない。
レビュー結果は`{plan_full_path}.review.md`に出力されるのでReadツールで読み取る。

## MCPもCLIも利用できない場合

計画ファイル提示前にユーザーに報告し、以下の選択を仰ぐ。

- codex CLIを別途セットアップし、codex MCPをインストールしてやり直す（推奨）
  - インストールコマンド: `claude mcp add --scope=user codex codex mcp-server`
- codex CLIを別途セットアップしてやり直す
- 当該環境ではcodexレビューを永続的にスキップする旨をプロジェクトのローカルメモファイルに記載してスキップする

## codexレビューの進め方

- codexの指摘にエージェント単独では採否を判断できないものが含まれる場合、その場でユーザーに確認して結論を確定させる
- codexの指摘がなくなるまで更新とレビューを繰り返す（指摘が的外れなものしかない場合は合格扱いでよい）
- 一度指摘ゼロになった後に限り、その後の軽微な修正では再レビューを省略してよい
- codex指摘1件が複数セクションに伝播する場合、関連セクションすべてを網羅的に更新する
- 計画ファイルを全面改訂した場合、過去のcodexセッション（MCPの`threadId`またはCLIの`SESSION_ID`）を破棄し、
  新規セッションで初回プロンプトからレビューをやり直す。
  旧セッションには改訂前の指摘文脈が残り、新方針と噛み合わない誤指摘の原因になるためである

## Windows環境での注意

codexはPowerShell経由でファイルを読み書きする際、デフォルトでShift-JISが使われ日本語が文字化けする。
対処としてプロンプトに以下を追記する。
「ファイルの読み書きはUTF-8エンコーディングを明示すること（例: `Get-Content -Encoding UTF8`）」
