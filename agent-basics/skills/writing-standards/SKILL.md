---
name: writing-standards
user-invocable: false
description: >
  ドキュメント・コメント・コード・テストコード・エージェント向け文書
  （`AGENTS.md`・`CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に最初に必ず呼び出す。
  AWI・UWIの本文起草時、成果物へ書く事実主張の裏付け調査時、ホスト機能の可否・入出力契約の調査時、
  記録の集計・分析時も呼び出す。
  規範・基準・手順・目標値の妥当性、到達性、有無、置き場所を確認する場面でも呼び出す。
  理解のためコードを読むだけの場合はトリガー不要。
# 編集時の注意点:
# 著者向けの品質基準だけを扱い、レビュー担当とレビューイーの判断指針はreview-standardsが定める。
# 規範本文はreferences/配下へ置き、本体には目的と読込条件だけを置く。
---

# 成果物の品質基準

品質判断の共通条件は`references/quality-principles.md`が定義する。読了済みで内容が変わっていない資料は再利用する。

本スキルはドキュメント、コード（hookなどの実装を含む）およびエージェント向け文書を書くエージェントへ品質基準を提供する。
レビューの判断基準は`agent-basics:review-standards`、計画ファイルの形式と作業工程には、対象プロジェクトの契約を適用する。

提示された文章やコードの品質は、参照できる資料から判断する。スキルフォルダーだけを読む環境でも[日本語の書き方](references/writing.md#日本語の書き方)へ到達できる。ファイルの変更やformatter・linter・testerの実行を伴う手順は、それらの機能を提供するホストで適用し、機能が無い場合は原稿と未実行のチェックを返す。実行と保存はそれらの機能を使えるClaude CodeまたはCodexで続ける。

以下の読込表は、行の条件が成立したら（作業の途中で成立した場合も）、その操作の前に同じ行の資料を全文読んで適用することを示す。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

<!-- read-table-source: SKILL-delivery-1.md -->

## 成果物種別ごとの必読資料

<!-- read-table-source: SKILL-delivery-2.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| 既存ファイルを編集・上書きする時、スクリプトで複数ファイルを機械変換する時 | `references/file-editing.md` |
| 成果物、報告・回答、委譲プロンプトへ書く事実主張を調査する時 | `references/investigation.md` |
| Xの投稿本文・日時・添付写真を資料として読む前 | `references/x-posts.md` |
| ホスト機能の可否・入出力契約を調べる時 | `references/host-official-references.md` |
| 計画の起草、実現方式の確定、実装の完了前と実行レビューで、変更後の設計品質を評価する時（エージェント向け文書だけの変更を含む）と、外部資料（論文、ベンチマーク、他プロジェクトの事例など）に基づく改善案を提案・評価する時 | `references/design-heuristics.md`の「設計案の予備選別」から「設計品質の劣化確認」まで（各節の適用条件を保つ） |
| 技術文書を書く時 | `references/writing.md`の「技術文書」 |
| 業務・仕様文書を書く時（AWI・UWIを含む） | `references/writing.md`の「業務・仕様文書」 |
| 体験を述べる文章を書く時 | `references/writing.md`の「体験を述べる文章」 |
| ユーザーの入力素材を整理して取り込む時 | `references/writing.md`の「ユーザー入力素材の取扱い」 |
| 配布物がエンドユーザーの環境でパッケージマネージャーへ渡す版指定（`uvx --from`の引数、`pyproject.toml`の`dependencies`、`uv run --with`の引数、PEP 723の`dependencies`など）を変える作業の起草、計画、実装、レビューの時 | `references/dependency-management.md` |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 起動時 | `references/quality-principles.md` | 起動時 | `skill:writing-standards` | |
| 人間が読む文章（ドキュメント、コメント、AWI・UWIの本文など）を書く時 | `references/writing.md`の「文章の共通規定」「読み手別の追加注意点」「構成・構造」「日本語の書き方」「改訂・保守」 | 起動時 | `skill:writing-standards` | |
| コード・テストコードを書く時 | 「コードの編集時に読む資料」の各行 | 判断 | `skill:writing-standards` | `same-document` |
| エージェント向け文書を書く時 | 「エージェント向け文書の編集時に読む資料」の各行 | 判断 | `skill:writing-standards` | `same-document` |
| Claude CodeでEditを使う時と、Writeで既存ファイルを上書きする前 | `references/file-editing-claude.md` | 操作 | `tool:Edit`、`tool:Write` | |

領域別の資料を選ぶときは、依頼の期待結果と変更する契約から該当する領域を特定する。この特定は読取先を選ぶ判断であり、具体的な設計案の比較・採用は該当節を読んだ後に行う。入力だけで領域が定まらなければ、対象の現物を読み取って確定してから該当節へ進む。文書の構成だけの判断など、領域の条件が成立しない場合は共通の設計基準を使う。節の取得には既存の読取操作を使い、読込表の指定節を読んでから設計へ戻る。

<!-- read-table-source: SKILL-delivery-3.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| 並行処理・排他制御を設計・変更・レビューする前 | `references/design-heuristics.md`の「並行処理・排他制御の設計観点」 |
| 検索・一覧画面を設計・変更・レビューする前 | `references/design-heuristics.md`の「検索・一覧画面の設計」 |
| 閾値と表示段階を設計・変更・レビューする前 | `references/design-heuristics.md`の「閾値と表示段階の設計」 |
| キャッシュを設計・変更・レビューする前 | `references/design-heuristics.md`の「キャッシュ設計」 |
| 非同期ジョブ・リトライを設計・変更・レビューする前 | `references/design-heuristics.md`の「非同期ジョブ・リトライ設計」 |
| DBスキーマ・マイグレーションを設計・変更・レビューする前 | `references/design-heuristics.md`の「DBスキーマ・マイグレーション設計」 |
| APIを設計・変更・レビューする前 | `references/design-heuristics.md`の「API設計」 |
| 認証・認可を設計・変更・レビューする前 | `references/design-heuristics.md`の「認証・認可設計」 |
| ファイルアップロードを設計・変更・レビューする前 | `references/design-heuristics.md`の「ファイルアップロード設計」 |
| 状態を変える多段手順の工程順を設計・変更・レビューする前 | `references/design-heuristics.md`の「状態を変える多段手順の工程順」 |

## 文章の作成時に読む資料

<!-- read-table-source: SKILL-delivery-4.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| 恒久的な成果物の文面案を執筆する前と、textlintの指摘へ対応する時 | `references/textlint-violations.md` |
| lint設定の緩和・無効化・除外指定を追加する時 | `references/lint-relax-criteria.md` |
| 文の口調を書き換える処置を選ぶ時（口語表現の警告、textlintのAI文体の指摘を含む）。正しい例と説明から、元の意味と条件を保つ書き方を選ぶ | `references/tone-examples.md`、`references/tone-examples-llm-tone.md` |
| 人間向け文書の役割と残す内容を選ぶ時 | `references/document-types.md` |
| 新しい概念名または識別子を導入する時 | `references/referent-table.md` |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `references/defined-names.md` |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 文章を書く時と表記をチェックする時（他の行より先に読む） | `references/notation-rules.md` | 起動時 | `skill:writing-standards` | |

## コードの編集時に読む資料

コードの編集とコードのレビュー（対象の差分について）に入る前に、`references/writing.md`の「文章の共通規定」「読み手別の追加注意点」「構成・構造」「日本語の書き方」「改訂・保守」と、次の技術別・工程別の表で該当する行の資料を読む。

<!-- read-table-source: SKILL-delivery-5.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| `py` | `references/python.md` |
| `ts`・`tsx` | `references/typescript.md` |
| `rs` | `references/rust.md` |
| `cs` | `references/csharp.md` |
| `sh`・`bash`・`sh.tmpl` | `references/bash.md` |
| `ps1`・`ps1.tmpl`・`psm1`・`psd1` | `references/powershell.md` |
| `cmd`・`bat` | `references/windows-batch.md` |
| `Dockerfile` | `references/dockerfiles.md` |
| `.github/workflows/*.yaml`・`.github/workflows/*.yml` | `references/github-actions.md` |
| `tailwindcss`（v4系） | `references/tailwindcss.md` |
| `alpinejs`（v3系。`<script>`読み込みを含む） | `references/alpinejs.md` |
| `@playwright/test`・`playwright`（Playwright Test v1系とPythonバインディング） | `references/playwright.md` |
| `sqlalchemy`（2.0系） | `references/sqlalchemy.md` |
| `drizzle-orm`・`drizzle-kit`（0.x系） | `references/drizzle.md` |
| `svelte`・`@sveltejs/kit`（Svelte 5・SvelteKit 2） | `references/svelte.md` |

<!-- read-table-source: SKILL-delivery-6.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `references/design-time.md` |
| コードを編集する時と、依存の追加・更新をする時 | `references/implementation-time.md` |
| 設計判断を確定する時、計画と実装を同じエージェントが続けて実施する時、コードレビューの時 | `references/design-heuristics.md`の「設計案の予備選別」から「設計品質の劣化確認」まで（各節の適用条件を保つ） |
| 依存の追加・更新、またはPythonの対応版を追加する時（CI・開発環境の版指定変更を含む） | `references/dependency-management.md` |
| MCPサーバーまたはホスト登録ツール（Function hooksなど）の説明、引数、応答を設計、実装、変更、レビューする時 | `references/mcp-server-design.md` |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `references/testing.md` |
| 文字エンコーディングを扱う時（日本語環境・ZIPファイル・Unicode正規化など） | `references/encoding.md` |
| 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボードなど）の作成・修正時 | `references/independent-html.md` |
| エンドユーザーが操作する画面の新設・変更、その計画またはレビューの時 | `references/ui-ux.md` |
| エンドユーザーが操作する画面をHTML、CSS、JavaScriptで実装またはレビューする時 | `references/ui-ux-web-rules.md` |
| エンドユーザーが操作する画面がフォーム、一覧・データ表、検索、通知、モーダル・パネル、AI機能、同意・解約または多言語表示を含む時 | `references/ui-ux-patterns.md` |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| hookスクリプトと機械チェックスクリプトを書く時 | 「エージェント向け文書の編集時に読む資料」の表のうち、hook、セッション状態および機械チェックスクリプトに関する行で条件が成立する行の資料 | 判断 | `skill:writing-standards` | `same-document` |

## エージェント向け文書の編集時に読む資料

エージェント向け文書の種別と操作に応じて、次の読込表を適用する。

<!-- read-table-source: SKILL-delivery-7.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| エージェント向け文書（AGENTS.md、CLAUDE.md、ルール、SKILL.md、サブエージェント定義、referencesなど）の編集前 | `references/writing.md`の「文章の共通規定」「読み手別の追加注意点」「構成・構造」「日本語の書き方」「改訂・保守」と`references/agent-documents-basics.md` |
| スキル編集 | `references/agent-skills.md`、`references/host-official-references.md` |
| エージェント向け文書の置き場所を選ぶ時 | `references/agent-documents-basics.md` |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `references/sub-agents.md` |
| hookスクリプトの新設・変更（入出力、matcher、環境変数） | `references/claude-hooks.md`、`references/host-official-references.md` |
| 遮断・警告を返すhookの判定を新設・変更する時と、hookを置くか、遮断と警告のどちらにするかを判定する時 | `references/claude-hooks-block-warn.md` |
| hookの出力フィールドを選ぶ時と、`PermissionRequest`・`UserPromptSubmit`のhookを新設・変更する時 | `references/claude-hooks-output.md` |
| `Stop`・`SubagentStop`のhookと、終了工程の証拠を新設・変更する時 | `references/claude-hooks-stop.md` |
| 遮断・警告・定型の通知を出力するhookを新設・変更する時と、hookなどの生成元がエージェントへ直接渡す本文（記述言語と`atk-auto`の標識）を新設・改訂する時 | `references/claude-hooks-messages.md` |
| auto modeのカスタムルール編集 | `references/auto-mode.md`、`references/host-official-references.md` |
| 機械チェックスクリプトの新設・改修 | `references/check-script-design.md` |
| エージェント向け文書へ新しい規定を追記する時と、文書の記述量を管理する時 | `references/agent-documents-additions.md` |

## 導入と適用

本プラグインは成果物の品質基準を提供する。必要な資料は各読込表の条件に従って読み、同じ版の読了済み本文は再利用する。作業の認可、委譲、記録の保存と工程管理には、対象プロジェクトが採用する手順を適用する。
