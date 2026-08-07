---
name: shell-exec
description: >
  コマンドライン作業をメインのコンテキストから分離して実行するときに起動する。
  複数のシェルコマンド実行を要する定型作業（gh・glabの操作など）で起動する。
context: fork
agent: Explore
model: haiku
allowed-tools: Bash
---

# シェル実行委譲

以下の指示に従い、コマンドをBashで実行して結果を報告する。

$ARGUMENTS

## 実行ルール

- 指示された操作のみを実行し、指示にない操作を追加しない
- 認証エラー・コマンド失敗時は出力をそのまま報告し、独自の回避策を試みない
- 報告は観測事実のみとし、指示で求められた情報を漏れなく含める
- `gh`や`glab`の操作など長出力が予想されるコマンド列を受け付ける
- CI失敗後のログ取得・要約は受け付ける。CI完了までの待機は受け付けない
  - 担当分離の理由は`agent-toolkit/skills/plan-mode/references/plan-impl-caller-reception.md`
    「pushとCI」節を参照する
  - 委譲元が`agent-toolkit:commit`の`references/push-and-ci.md`に従い、自身で待機する
- コマンドの生出力はfork内で確認し、メインへの報告へ全文転記しない
- 終了状態、警告、依頼で指定された値、後続判断に必要な要約を報告する
- 失敗原因の特定に必要な行だけを原文のまま添える
