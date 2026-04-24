# 計画ファイルのcodexレビュー

ユーザーに計画ファイルを提示する前に、codexでレビューする。
plan modeの制約下でもcodexレビューは制約対象外として扱える（計画ファイル自体のレビューのため）。

codexレビューは計画ファイル自体に対するレビュー工程であり、
計画ファイルの`## 実行方法`節のセルフレビューとは別物である。
codexの実行状況・スキップ判断・指摘対応履歴を計画ファイル本文へ記載する必要はなく、
メインとユーザー間のやり取りで完結させる。

## MCPツール版（優先）

`mcp__codex__codex` / `mcp__codex__codex-reply` が利用可能なら優先して使う。

- 初回: `mcp__codex__codex` を以下のパラメーターで呼び出す
  - `cwd`: `"{project_directory}"`
  - `sandbox`: `"danger-full-access"`
  - `prompt`: 以下の文言をそのまま使う（CLIフォールバック時も同一文言を使用する）

    ```text
    {plan_full_path} この計画ファイルをレビューして。
    以下の手順で行うこと。
    1. 考えられる指摘候補を内部的に網羅列挙する
    2. そこから確実に問題である致命的かつ本質的な欠陥のみを残し最終応答とする
    3. 今回の応答で全て出し切り、再レビューで追加の新規指摘を出さない

    些末な指摘、不要な提案、推測による指摘は禁止。
    計画ファイルの記述スタイル（構成・表現・書き方）に対する指摘も禁止。（記述間の矛盾は除く）
    実装時にエージェントが判断可能な細部（変数名・エラーメッセージ文言・ループ構造・小規模な制御フローなど）への指摘は禁止。
    `agent-toolkit:*`スキルや`careful-*-reviewer`などの識別子はClaude Code側の仕組みでcodex側からは存在確認できないため、これらの実在性に関する指摘は不要。
    指摘する場合は修正方針も簡潔に併記すること。
    指摘が無い場合は指摘無しと応答すること。
    ```

- 2回目以降: `mcp__codex__codex-reply` を以下のパラメーターで呼び出す
  - `threadId`: 前回の戻り値から取得したthreadId
  - `prompt`: `計画ファイルを更新したので再レビューして。`

## CLIフォールバック版

MCPツールが利用できない場合はcodex CLIを試す。

### 初回実行

`--cd`でプロジェクトディレクトリを指定し、計画ファイルのパスを引数に与える。
`--output-last-message`でレビュー結果をファイルに書き出す。
codexの出力（session id含む）はstderrに出るため、`2>&1 | grep` でsession id行のみClaudeへ返す。
`set -o pipefail` によりcodexが失敗した場合はその終了コードがそのままシェルに返る。

プロンプト文言（`{初回プロンプト}`）は上述の「MCPツール版（優先）」の初回`prompt`の内容。

```bash
set -o pipefail && codex exec --dangerously-bypass-approvals-and-sandbox --cd "{project_directory}" --output-last-message "{plan_full_path}.review.md" \
  "{初回プロンプト}" \
  2>&1 | grep "^session id:"
```

session idの抽出に失敗した場合やcodexがエラー終了した場合は、
grepを外して `codex exec ... 2>&1` で再実行し全文を確認する。

### 2回目以降

`exec resume`を使用して前回のレビューから続行する。
`SESSION_ID` は初回コマンドの `grep "^session id:"` で抽出された `session id:` 行から取得する。
`{plan_full_path}.review.md` には含まれない。
注意: `--last` は並列セッション実行時に意図しないセッションを再開する恐れがあるため使用しない。

プロンプト文言（`{再レビュープロンプト}`）は上述の「MCPツール版（優先）」の2回目以降`prompt`の内容。

```bash
codex exec resume --dangerously-bypass-approvals-and-sandbox --output-last-message "{plan_full_path}.review.md" {SESSION_ID} \
  "{再レビュープロンプト}"
```

### レビュー結果の読み取り

レビュー結果は `{plan_full_path}.review.md` に出力されるのでReadツールで読み取る。

## MCPもCLIも使えない場合

計画ファイル提示前にユーザーに状況を報告し、以下の選択を仰ぐ。

- codex CLIを別途セットアップし、codex MCPをインストールしてやり直す（推奨）
  - インストールコマンド: `claude mcp add --scope=user codex codex mcp-server`
- codex CLIを別途セットアップしてやり直す
- 当該環境ではcodexレビューを永続的にスキップする旨をプロジェクトの `CLAUDE.local.md` に記載してスキップする
  - 当該ファイルはgitignore推奨

## codexレビューの進め方

- ユーザーへの確認事項がある場合は必ずcodexレビュー前にユーザーに確認し結論を確定させておく
  - ただし、方針レベルの合意を実装詳細レベルの合意と誤解しないよう注意
- codexレビューでユーザー判断な指摘があった場合、その場ですぐにユーザーに確認して結論を確定させる
- codexの指摘がなくなるまで更新とレビューを繰り返す
  - ただし、合否判断は呼び元が行ってよい（指摘が的外れなものしかない場合なども合格扱いでよい）
- 一度指摘ゼロになった後に限り、その後の軽微な修正では再レビューを省略してよい
  - 方針レベルの変更時は再レビューする
- codexの指摘に基づいて計画ファイルを修正する際は、どのような修正をするかユーザーに伝える

## Windows環境での注意

codexはPowerShell経由でファイルを読み書きする際、デフォルトでShift-JISが使われて日本語が文字化けする。
対処として、codexへのプロンプトに以下を追記する。
「ファイルの読み書きはUTF-8エンコーディングを明示すること（例: `Get-Content -Encoding UTF8`）」
