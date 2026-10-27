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

品質判断の共通基準は`references/quality-principles.md`で定義する。すでに読み込み済みで内容が変わっていない資料は、再取得せずにそのまま利用する。

本スキルは、ドキュメントやコード（hookなどの実装を含む）、エージェント向け文書を作成するエージェントに対して品質基準を提供する。
レビュー時の判断基準には`agent-basics:review-standards`を用い、計画ファイルの形式や作業工程には対象プロジェクトの取り決めを適用する。

文章やコードの品質は、参照できる資料に基づいて判断する。スキルフォルダーのみを読み込める環境であっても、[日本語の書き方](references/writing.md#日本語の書き方)を参照できる。ファイルの変更やformatter・linter・testerの実行を伴う手順は、それらの機能が利用できるホスト環境で適用する。機能がない環境では、作成した原稿と未実行のチェック項目を結果として返す。実際の変更や保存は、それらの機能を使えるClaude CodeやCodexで継続して行う。

以下の読込表に記載された条件に当てはまる場合は（作業の途中で該当した場合を含む）、対象の操作をする前に、該当行に示された資料をすべて読んで内容を反映する。

## 名指し参照の読込方法

本文の各参照における適用条件を確認し、指定された資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断をする。起動時の条件に該当する資料は、起動時のプロンプトとともに渡される。

<!-- read-table-source: SKILL-delivery-1.md -->

## 成果物種別ごとの必読資料

<!-- read-table-source: SKILL-delivery-2.md -->

| 時点または条件 | 資料と注目箇所 |
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

領域別の資料を選ぶときは、依頼が求める結果と変更対象のインターフェースや仕様から該当する領域を特定する。ここでの特定は読むべき資料を決めるためのものであり、具体的な設計案の比較や採用は該当節を読んだ後に行う。入力内容だけから領域を判断できない場合は、対象ファイルの実物を読み取って確認してから該当節に進む。文書の構成のみを判断する場合のように領域の条件に当てはまらないときは、共通の設計基準を用いる。資料の取得には通常の読取操作を使い、読込表で指定された節を読んでから設計の検討に戻る。

<!-- read-table-source: SKILL-delivery-3.md -->

| 時点または条件 | 資料と注目箇所 |
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

| 時点または条件 | 資料と注目箇所 |
| --- | --- |
| textlintなど文章チェックの診断へ対応する時 | `references/textlint-violations.md` |
| 文章の書き直しを委ねる時 | `references/textlint-violations.md`の「文の書き直しの委譲と検収」 |
| 独立したレビューを経ずに成果物を完成させる時（再レビューを省く修正、独立したレビューを起動しない修正経路を含む）と、レビュー限定基準の指摘を受けて採否を判断する時 | `references/review-only-criteria.md` |
| lint設定の緩和・無効化・除外指定を追加する時 | `references/lint-relax-criteria.md` |
| 文の口調を書き換える処置を選ぶ時（口語表現の警告、textlintのAI文体の指摘を含む）。正しい例と説明から、元の意味と条件を保つ書き方を選ぶ | `references/tone-examples.md`、`references/tone-examples-llm-tone.md` |
| 人間向け文書の役割と残す内容を選ぶ時 | `references/document-types.md` |
| 新しい概念名または識別子を導入する時 | `references/referent-table.md` |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `references/defined-names.md` |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 文章を書く時と表記をチェックする時（他の行より先に読む） | `references/notation-rules.md` | 起動時 | `skill:writing-standards` | |

## コードの編集時に読む資料

コードの編集やコードレビュー（変更差分の確認）に着手する前に、`references/writing.md`の「文章の共通規定」「読み手別の追加注意点」「構成・構造」「日本語の書き方」「改訂・保守」と、以降の技術別・工程別の表で該当する資料を読む。

<!-- read-table-source: SKILL-delivery-5.md -->

| 時点または条件 | 資料と注目箇所 |
| --- | --- |
| `py` | `references/python.md`の「言語スタイル」「テストコード（pytest）」「Python新構文と導入バージョン」 |
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

| 時点または条件 | 資料と注目箇所 |
| --- | --- |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `references/design-time.md` |
| コードを編集する時と、依存の追加・更新をする時 | `references/implementation-time.md` |
| 設計判断を確定する時、計画と実装を同じエージェントが続けて実施する時、コードレビューの時 | `references/design-heuristics.md`の「設計案の予備選別」から「設計品質の劣化確認」まで（各節の適用条件を保つ） |
| 依存の追加・更新、またはPythonの対応版を追加する時（CI・開発環境の版指定変更を含む） | `references/dependency-management.md` |
| MCPサーバーまたはホスト登録ツール（Function hooksなど）の説明、引数、応答を設計、実装、変更、レビューする時 | `references/mcp-server-design.md` |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `references/testing.md` |
| Pythonの静的解析（ruff・pylint・pyright・mypyなど）の診断へ対処する時と、抑制を追加する時 | `references/python.md`の「静的解析の誤検出と抑制」 |
| 文字エンコーディングを扱う時（日本語環境・ZIPファイル・Unicode正規化など） | `references/encoding.md` |
| 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボードなど）の作成・修正時 | `references/independent-html.md` |
| エンドユーザーが操作する画面の新設・変更、その計画またはレビューの時 | `references/ui-ux.md` |
| エンドユーザーが操作する画面をHTML、CSS、JavaScriptで実装またはレビューする時 | `references/ui-ux-web-rules.md` |
| エンドユーザーが操作する画面がフォーム、一覧・データ表、検索、通知、モーダル・パネル、AI機能、同意・解約または多言語表示を含む時 | `references/ui-ux-patterns.md` |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| hookスクリプトと機械チェックスクリプトを書く時 | 「エージェント向け文書の編集時に読む資料」の表のうち、hook、セッション状態および機械チェックスクリプトに関する行で条件が成立する行の資料 | 判断 | `skill:writing-standards` | `same-document` |

## エージェント向け文書の編集時に読む資料

エージェント向け文書の種類や行う操作に応じて、次の読込表を適用する。

<!-- read-table-source: SKILL-delivery-7.md -->

| 時点または条件 | 資料と注目箇所 |
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

本プラグインは成果物の品質基準を定めている。各読込表の条件に従って必要な資料を読み、同じバージョンの資料をすでに読んでいる場合はその内容を再利用する。作業の認可や委譲、記録の保存、工程管理については、対象プロジェクトで採用されている手順に従う。
