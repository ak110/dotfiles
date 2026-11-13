# Claude Codeでの委譲の実装：起動パラメーター

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲入力の言語指定を確定する前 | `base-contract-operations.md` | 判断 | `delegation`、`read:agent-toolkit:*/skills/delegation/references/base-contract-operations.md` |

## 起動パラメーター

`Agent`ツールを使用する条件は`references/runtime-routing.md`「実行手段」に従う。
モデル、推論の深さ、バックグラウンド実行、デフォルト値は、起動時に公開されているスキーマおよび定義ファイルのfrontmatterに基づいて決定する。
非対話起動では、通知後の同一セッション再開が実行環境から保証される場合だけ、応答終了による待機を使う。
保証されない場合は同期起動と公開待機手段で子の結果を回収し、報告の同期やfinishまで完了する。
`atk run-skill`はClaudeの子環境だけに`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`を渡し、利用上限解除後の再開にも適用する。

`routing-fork.md`「会話を引き継ぐ委譲」でforkを選択し、かつfork modeが有効な場合は、`Agent`ツールに`subagent_type: "fork"`を指定する。
forkは親エージェントのモデルと会話履歴を引き継いでバックグラウンドで実行され、通常のサブエージェントと同様の完了通知で結果を返し、親からの1階層のみで完結する。
fork modeは対話型セッションではデフォルトで有効であるが、`-p`による非対話実行やAgent SDK環境では無効である。
無効な環境では、`agents_server`による新規起動を利用する。
監査記録は`docs/development/audit-records-delegation.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：起動パラメーター：2026年9月27日」に記載されている。

## 委譲プロンプトへ含める前提

以下の`Agent`・`Explore`・`Plan`の操作説明は、`runtime-routing.md`「実行手段」の使用条件を満たす場合に適用する。

- 専用の`<役割名>.subagent.md`またはagent定義が存在する起動では、委譲プロンプトは`delegation-sending.md`「送信」に従って構成する。
  完了報告をツールの戻り値として1回だけ返し、`SendMessage`では送信しないという契約は該当の定義側に記述する。
  即時通知の扱いは、`agent-toolkit/share/rules/rules-subagent.md`「確認事項の即時通知」および`agent-toolkit/share/rules/rules-subagent.claude-code.md`「即時通知の宛先」に従う。
- 名前付き定義には、前景での完了報告の返却方法、`SendMessage`を使用しないこと、待機対象の結果を含める要件を規定する。
  記述言語は`agent-toolkit/share/rules/common/01-agent.md`「使用言語」の読者基準に従い、フォーマットを固定する機械可読ブロックや固定文字列はそのまま保持する。
- 専用の定義を持たない`claude`・`Explore`・forkの委譲プロンプトには、待機対象の結果も含めてツールの戻り値で1回だけ返却する（`SendMessage`は使用しない）旨の指示を含める。
  ユーザーが読む本文・成果物の言語指定は`base-contract-operations.md`「操作の制限と応答言語」に従う。
  なお、forkでは親エージェントが保持している背景情報の再掲は省略する。
- `rules-subagent.md`および`rules-subagent.claude-code.md`は`SubagentStart`フックによって自動的に追加されるため、プロンプト内で明示的に読み込みを指示する必要はない。
- 組み込みの`Explore`および`Plan`は、親エージェントのCLAUDE.md、メモリー階層、親のgitステータスを引き継がない（同名のユーザー定義やプロジェクト定義で上書きした場合は引き継ぐ。これはfrontmatterでは変更できない固定の挙動である）。
  そのため、委譲プロンプトに`01-agent.md`の絶対パスを渡して作業着手前に全文読み込ませ、判定に使用する実行系コマンド、検証コマンド、除外範囲、報告様式を明記する。
- 委譲先がさらに読み取り専用の調査やレビューを`Agent`ツールで起動する場合は、起動前にmanaged-temp配下の成果物ファイルの絶対パスを確定し、委譲プロンプトに含める。
  記載形式は、定義ファイルが存在する場合はその完了報告全体（委譲プロンプト側は参照のみを保持する）とし、定義ファイルがない場合は完了報告形式を委譲プロンプトに記述する。
  成果物ファイルを指定された委譲先は、返信先の解決可否にかかわらず、必須項目をすべて含む最終完了報告をそのファイルに書き込んでから終了する。
  ファイルへの書き込みに失敗した場合は成功を返さず、通常の完了報告内に失敗を含めて報告する。
  要約、部分的な書き込み、必須項目の欠落がある場合は未完了として扱う。

## 計画ファイル作成のplan mode下委譲

サンドボックスパスへの書き込み内容をメインエージェントが検収した上で、正規パス（`~/.claude/plans/`配下）へ反映する。
正規パスに存在するファイルの削除や移動は、メインエージェントのみが実行する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 委譲の基準を適用する前 | `../SKILL.md` |
| 委譲：送信の基準を適用する前 | `delegation-sending.md` |
