---
name: writing-standards
user-invocable: false
description: >
  ドキュメント・コメント・コード・テストコード・エージェント向け文書
  （`AGENTS.md`・`CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に最初に必ず呼び出す。
  AWI・UWIの本文起草時、成果物へ書く事実主張の裏付け調査時、ホスト機能の可否・入出力契約の調査時、
  セッション記録（`~/.claude/projects`配下）の集計・分析時も呼び出す。
  規範・基準・手順・目標値の妥当性、到達性、有無、置き場所を確認する場面でも呼び出す。
  理解のためコードを読むだけの場合はトリガー不要。
# 編集時の注意点:
# 著者向けの品質基準だけを扱い、レビュー担当とレビューイーの判断指針はreview-standardsが定める。
# 規範本文はreferences/配下へ置き、本体には目的と読込条件だけを置く。
---

# 成果物作成時のtoolkit運用

品質基準と技術別の読込表は`agent-basics:writing-standards`が定義する。
同じ版の取得済み本文は再利用し、次の読込表ではtoolkitに残る運用条件を補う。
toolkitの公開スキルを使う既存の呼び出しは、このスキルで品質と運用の両方へ到達する。

## 運用の読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。
品質だけが必要な場面では正式なagent-basicsのスキルと資料を使う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 起動時 | `agent-basics:writing-standards/SKILL.md` | 起動時 | `skill:writing-standards` | |
| エージェント向け文書の責務と配置を決める前 | `references/agent-documents-basics.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/agent-documents-basics.md` | |
| 成果物、報告・回答、委譲プロンプトへ書く事実主張を調査する時 | `references/investigation.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/investigation.md` | |
| セッション記録の集計・分析 | `references/session-records.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/session-records.md` | |
| 人間が読む文章（ドキュメント、コメント、AWI・UWIの本文など）を書く時 | `references/writing.md`の「文章の共通規定」 | 起動時 | `skill:writing-standards` | |
| ユーザーの入力素材を整理して取り込む時 | `references/writing.md`の「ユーザー入力素材の取扱い」 | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/writing.md#「ユーザー入力素材の取扱い」` | |
| 文章を書く時と表記をチェックする時（他の行より先に読む） | `references/notation-rules.md` | 起動時 | `skill:writing-standards` | |
| 恒久的な成果物の文面案を執筆する前と、textlintの指摘へ対応する時 | `references/textlint-violations.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/textlint-violations.md` | |
| 人間向け文書の役割と残す内容を選ぶ時 | `references/document-types.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/document-types.md` | |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `references/defined-names.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/defined-names.md` | |
| `py` | `references/python.md` | 操作 | `path:*.py` | |
| `ps1`・`ps1.tmpl`・`psm1`・`psd1` | `references/powershell.md` | 操作 | `path:*.ps1`、`path:*.ps1.tmpl`、`path:*.psm1`、`path:*.psd1` | |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `references/design-time.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/design-time.md` | |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `references/testing.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/testing.md` | |
| エンドユーザーが操作する画面の新設・変更、その計画またはレビューの時 | `references/ui-ux.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/ui-ux.md` | |
| スキル編集 | `references/agent-skills.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/agent-skills.md` | |
| エージェント向け文書の置き場所を選ぶ時 | `references/delivery-scope.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/delivery-scope.md` | |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `references/sub-agents.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/sub-agents.md` | |
| hookスクリプトの新設・変更（入出力、matcher、環境変数） | `references/claude-hooks.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/claude-hooks.md` | |
| 遮断・警告を返すhookの判定を新設・変更する時と、hookを置くか、遮断と警告のどちらにするかを判定する時 | `references/claude-hooks-block-warn.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/claude-hooks-block-warn.md` | |
| hookの出力フィールドを選ぶ時と、`PermissionRequest`・`UserPromptSubmit`のhookを新設・変更する時 | `references/claude-hooks-output.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/claude-hooks-output.md` | |
| `Stop`・`SubagentStop`のhookと、終了工程の証拠を新設・変更する時 | `references/claude-hooks-stop.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/claude-hooks-stop.md` | |
| 遮断・警告・定型の通知を出力するhookを新設・変更する時と、hookなどの生成元がエージェントへ直接渡す本文（記述言語と`atk-auto`の標識）を新設・改訂する時 | `references/claude-hooks-messages.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/claude-hooks-messages.md` | |
| auto modeのカスタムルール編集（権限拒否に遭遇した場面は`agent-toolkit:user-confirmation-and-report`が扱う） | `references/auto-mode.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/auto-mode.md` | |
| セッション状態ファイルまたはフラグを扱う編集（hook編集とauto modeのカスタムルール編集で扱う場合を含む） | `references/session-state-and-flags.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/session-state-and-flags.md` | |
| 機械チェックスクリプトの新設・改修 | `references/check-script-design.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/check-script-design.md` | |
| エージェント向け文書へ新しい規定を追記する時と、文書の記述量を管理する時 | `references/agent-documents-additions.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/agent-documents-additions.md` | |
