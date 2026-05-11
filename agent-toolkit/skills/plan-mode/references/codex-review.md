# 計画ファイルのcodexレビュー

`mcp__codex__codex` / `mcp__codex__codex-reply` が利用可能ならMCPツール版を優先する。
利用不可ならCLIフォールバック版を使う。

## プロンプト

初回プロンプトは以下の文言をそのまま使う（MCP・CLI共通）。

```text
{plan_full_path} この計画ファイルをレビューして。
以下の手順で行うこと。
1. 考えられる指摘候補を内部的に網羅列挙する
2. そこから確実に問題である致命的かつ本質的な欠陥のみを残し最終応答とする
3. 今回の応答で全て列挙し、再レビューで追加の新規指摘を提示しない

些末な指摘、不要な提案、推測による指摘は禁止。
計画ファイルの記述スタイル（構成・表現・書き方）に対する指摘も禁止。（記述間の矛盾は除く）
実装時にエージェントが判断可能な細部（変数名・エラーメッセージ文言・ループ構造・小規模な制御フローなど）への指摘は禁止。
`agent-toolkit:*`スキルや`plan-*-reviewer`などの識別子はClaude Code側の仕組みでcodex側からは存在確認できないため、これらの実在性に関する指摘は不要。
指摘する場合は修正方針も簡潔に併記すること。
指摘が無い場合は指摘無しと応答すること。
```

2回目以降は `計画ファイルを更新したので再レビューして。` を渡す。

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

- ユーザーへの確認事項がある場合は必ずcodexレビュー前にユーザーに確認し結論を確定させておく
- codexの指摘にエージェント単独では採否を判断できないものが含まれる場合、その場でユーザーに確認して結論を確定させる
- codexの指摘がなくなるまで更新とレビューを繰り返す（指摘が的外れなものしかない場合は合格扱いでよい）
- 一度指摘ゼロになった後に限り、その後の軽微な修正では再レビューを省略してよい
- codex指摘1件が複数セクションに伝播する場合、関連セクションすべてを網羅的に更新する
- ファイルパスの実在確認・記述間の単純な不整合など初歩的整合性チェックは計画提出前にメイン側で完了させる。
  codexは設計上の論点・矛盾検出に集中させる前提のため、これら初歩チェックを肩代わりさせない

## Windows環境での注意

codexはPowerShell経由でファイルを読み書きする際、デフォルトでShift-JISが使われ日本語が文字化けする。
対処としてプロンプトに以下を追記する。
「ファイルの読み書きはUTF-8エンコーディングを明示すること（例: `Get-Content -Encoding UTF8`）」
