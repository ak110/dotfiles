---
name: writing-standards
user-invocable: false
description: >
  ドキュメント・コメント・コード・テストコード・エージェント向け文書
  （`AGENTS.md`・`CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に最初に必ず呼び出す。
  作業項目の本文の起草時、成果物へ書く事実主張の裏付け調査時、ホスト機能の可否・入出力契約の調査時、
  記録の集計・分析時も呼び出す。
  規範・基準・手順・目標値の妥当性、到達性、有無、置き場所を確認する場面でも呼び出す。
  成果物の根拠として外部資料を参照するときは、その取得を始める前に起動する。
  GitLab CI設定の編集・確認・キーワード仕様・典型パターン・lint実行方法の参照時、
  pytilpackのモジュール構成・API・extras・CLIの参照時、`import pytilpack`を含むコードの読み書き時と、
  pytilpackへ依存するプロジェクトでPythonの汎用的な処理を新しく実装する時も起動する。
  これらを除く理解のためのコード読取では起動不要。
# 編集時の注意点:
# 著者向けの品質基準だけを扱い、レビュー担当とレビューイーの判断指針はreview-standardsが定める。
# 規範本文はreferences/配下へ置き、本体には目的と読込条件だけを置く。
---

# 成果物の品質基準

品質判断の共通基準は`references/quality-principles.md`で定義する。
すでに読み込み済みで内容が変わっていない資料は、再取得せずにそのまま利用する。

外部資料を根拠に使う依頼では、取得前に資料種別に合う既存の取得手順を選ぶ。
`agent-basics:fetch-sources`へ接続し、成功条件と取得範囲を確認してから取得結果を根拠に使う。

本スキルは、ドキュメントやコード（hookなどの実装を含む）、エージェント向け文書を作成するエージェントに対して品質基準を提供する。
レビュー時の判断基準には`agent-basics:review-standards`を用い、計画ファイルの形式や作業工程には対象プロジェクトの取り決めを適用する。

文章やコードの品質は、参照できる資料に基づいて判断する。
スキルフォルダーのみを読み込める環境であっても、[日本語の書き方](references/writing.md)を参照できる。
ファイルの変更やformatter・linter・testerの実行を伴う手順は、それらの機能が利用できるホスト環境で適用する。
機能がない環境では、作成した原稿と未実行のチェック項目を結果として返す。
実際の変更や保存は、それらの機能を使えるClaude CodeやCodexで継続して行う。

用途別の資料は、文章全般に適用する共通基準と「文書種別ごとの注意点」を組み合わせて読む。

次の操作、成果物の種類、判断する事項から読込表の条件を選び、判断前に指定資料の全文を読む。
hookの通知は選択を補助する。
同じ版の取得済み本文は再利用し、会話圧縮後は残る作業に必要な範囲を選び直す。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 成果物や提案のレビュー、指摘の採否を判断する前 | `../review-standards/SKILL.md` |

## 成果物種別ごとの必読資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 既存ファイルを編集・上書きする時、スクリプトで複数ファイルを機械変換する時 | `references/file-editing.md` | 操作 | `existing-file-edit` | |
| 成果物、報告・回答、委譲プロンプトへ書く事実主張を調査する時 | `references/investigation.md`、`references/investigation-sources.md` | | | |
| 観測で前提が崩れ、次の確認や方針を選ぶ時 | `references/investigation-hypotheses.md` | | | |
| 現物の観測に基づいてエージェント向け文書の技術的な条文を新設・改訂する時 | `references/investigation-technical-clauses.md` | | | |
| 実行・測定・検証の結果を報告または判定する時（エージェントCLIや共有状態を扱うツールを検証のために実行する時を含む） | `references/investigation-results.md` | | | |
| X投稿などの外部資料の本文・日時・添付画像を取得・確認する前 | `../fetch-sources/SKILL.md` | | | |
| ホスト機能の可否・入出力契約を調べる時 | `references/host-official-references.md` | | | |
| 計画の起草、実現方式の確定、実装の完了前と実行レビューで、変更後の設計品質や累積したファイル構成を評価する時（エージェント向け文書だけの変更を含む）と、外部資料（論文、ベンチマーク、他プロジェクトの事例など）に基づく改善案を提案・評価する時 | `references/design-heuristics-screening.md`、`references/design-heuristics-necessity.md`、`references/design-heuristics-mechanisms.md`、`references/design-heuristics-responsibility.md`、`references/design-heuristics-proportionality.md`、`references/design-heuristics-decisions.md`、`references/design-heuristics-quality.md` | | | |
| 技術記事・操作手順などの技術文書を書く時 | `references/writing-technical.md` | | | |
| 業務・仕様文書を書く時（変更説明、障害報告、提案書、議事録、規程、作業項目などを含む） | `references/writing-business.md` | | | |
| 体験を述べる文章を書く時 | `references/writing-experience.md` | | | |
| スライドを書く時 | `references/writing-slides.md` | | | |
| 紹介・宣伝文を書く時 | `references/writing-promotion.md` | | | |
| メール・チャットを書く時 | `references/writing-messages.md` | | | |
| 論文・調査報告を書く時 | `references/writing-research.md` | | | |
| 脚本・台本を書く時 | `references/writing-scripts.md` | | | |
| ユーザーの入力素材を整理して取り込む時 | `references/input-material.md` | | | |
| 依存パッケージ、コンテナーイメージ、言語処理系、CI・自動ビルドのツールやactionの版指定を選ぶ・変える作業の起草、計画、実装、レビューの時。配布物が渡す`uvx --from`、`dependencies`、`uv run --with`、PEP 723の版指定やPython対応版の追加も含む | `references/dependency-management.md` | | | |
| ロゴ、ワードマーク、ブランドマークとそのアイコン展開を制作・改訂する時（その計画・作業項目の起草を含む） | `references/logo-quality.md` | | | |
| エンドユーザーが操作する画面の新設・変更、その計画・作業項目の起草またはレビューの時 | `references/ui-ux.md` | | | |
| Web性能の改善・設計・レビューを行う時 | `references/ui-ux-accessibility.md`、`references/ui-ux-web-rules.md` | | | |
| エンドユーザーが操作する画面の新設・変更、その計画・作業項目の起草またはレビューで、フォーム、一覧・データ表、検索、通知、モーダル・パネル、AI機能、自動で切り替わる表示、同意・解約または多言語表示を含む時 | `references/ui-ux-patterns.md` | | | |
| ユーザーが法令上の義務やプライバシーの確認を依頼した時と、不特定のエンドユーザーへ公開・提供するWebサービスやアプリで、第三者サーバーからのリソース読み込みや解析・広告タグ、操作や入力の記録、宣伝メールの一斉送信、定期課金、エンドユーザーの投稿の公開または会員登録を新設・変更する時（その計画・作業項目の起草またはレビューを含む）。自分用ツール、社内システム、ユーザーが自分で開く単体HTML成果物は、確認の依頼と社外の不特定のエンドユーザーへ公開範囲を広げる変更の場合だけ該当する | `references/public-service-obligations.md` | | | |
| 起動時 | `references/quality-principles.md` | 起動時 | `skill:writing-standards` | |
| 人間が読む文章（ドキュメント、コメント、作業項目の本文など）を書く時 | `references/writing.md`、`references/writing-common.md`、`references/writing-readers.md`、`references/writing-genres.md`、`references/writing-maintenance.md` | 操作 | `body-writing` | |
| コード・テストコードを書く時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `skill:writing-standards` | `same-document` |
| エージェント向け文書を書く時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `skill:writing-standards` | `same-document` |
| Claude CodeでEditを使う時と、Writeで既存ファイルを上書きする前 | `references/file-editing-claude.md` | 操作 | `claude-existing-file-edit` | |

領域別の資料を選ぶときは、依頼が求める結果と変更対象のインターフェースや仕様から該当する領域を特定する。
ここでの特定は読むべき資料を決めるためのものであり、具体的な設計案の比較や採用は該当節を読んだ後に行う。
入力内容だけから領域を判断できない場合は、対象ファイルの実物を読み取って確認してから該当節に進む。
文書の構成のみを判断する場合のように領域の条件に当てはまらないときは、共通の設計基準を用いる。
資料の取得には通常の読取操作を使い、読込表で指定された資料の全文を読んでから設計の検討に戻る。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 並行処理・排他制御を設計・変更・レビューする前 | `references/design-runtime-concurrency.md` |
| 検索・一覧画面を設計・変更・レビューする前 | `agent-basics:writing-standards/references/design-display.md` |
| 閾値と表示段階を設計・変更・レビューする前 | `agent-basics:writing-standards/references/design-display.md` |
| キャッシュを設計・変更・レビューする前 | `references/design-runtime-cache.md` |
| 負荷・データ・外部システムの条件に依存する技術仕様や非機能要件を設計・変更・レビューする前 | `references/design-runtime-environment.md` |
| 非同期ジョブ・リトライを設計・変更・レビューする前 | `references/design-runtime-jobs.md` |
| DBスキーマ・マイグレーションを設計・変更・レビューする前 | `references/design-data-interfaces-schema.md` |
| APIを設計・変更・レビューする前 | `references/design-data-interfaces-api.md` |
| 認証・認可を設計・変更・レビューする前 | `references/design-data-interfaces-authorization.md` |
| ファイルアップロードを設計・変更・レビューする前 | `references/design-data-interfaces-uploads.md` |
| 状態を変える多段手順の工程順を設計・変更・レビューする前 | `references/design-runtime-procedure-order.md` |
| テストの網羅範囲の管理方法を設計・変更・レビューする前 | `references/test-coverage-ledger.md` |
| 画面を操作する探索的な試行の実施要否を決める前 | `references/testing-selection.md` |
| DBトランザクションを設計・変更・レビューする前 | `references/design-data-interfaces-transactions.md` |
| クエリ性能を設計・変更・レビューする前 | `references/design-data-interfaces-queries.md` |
| DB更新と外部送信の整合性を設計・変更・レビューする前 | `references/design-data-interfaces-transactions.md` |
| Webhook受信を設計・変更・レビューする前 | `references/design-data-interfaces-api.md` |
| 常駐サービスの設計・変更・レビュー、または子プロセス・委譲先・非同期ジョブなど配下を持つ機能の停止・再開・切替を要件化・設計・変更・レビューする前 | `references/design-runtime-services.md` |
| 負荷制御と外部呼び出しを設計・変更・レビューする前 | `references/design-runtime-services.md` |

## 技術資料の確認・参照・読取

| 時点または条件 | 全文読む資料 |
| --- | --- |
| `.gitlab-ci.yml`の編集・確認時と、GitLab CIのキーワード仕様・典型パターン・lint実行方法を参照する時 | `references/gitlab-ci.md` |
| `import pytilpack`を含むコードを読み書きする時、pytilpackのモジュール構成・API・extras・CLIの使い方を参照する時と、pytilpackへ依存するプロジェクトでPythonの汎用的な処理を新しく実装する前 | `references/pytilpack.md` |

## 文章の作成時に読む資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| textlintなど文章チェックの診断へ対応する時 | `references/textlint-violations.md` | | | |
| 文章の書き直しを委ねる時 | `agent-basics:writing-standards/references/textlint-violations.md` | | | |
| 独立したレビューを経ずに成果物を完成させる時（再レビューを省く修正、独立したレビューを起動しない修正経路を含む）と、レビュー限定基準の指摘を受けて採否を判断する時 | `references/review-only-criteria.md` | | | |
| lint設定の緩和・無効化・除外指定を追加する時 | `references/lint-relax-criteria.md` | | | |
| 文の口調を書き換える処置を選ぶ時（口語表現の警告、textlintのAI文体の指摘を含む）。正しい例と説明から、元の意味と条件を保つ書き方を選ぶ | `references/tone-examples.md` | | | |
| 文の口調を書き換える処置を選ぶ時（口語表現の警告、textlintのAI文体の指摘を含む）。正しい例と説明から、元の意味と条件を保つ書き方を選ぶ | `references/tone-examples-llm-tone.md` | | | |
| 人間向け文書の役割と残す内容を選ぶ時 | `references/document-types.md` | | | |
| 設計の記録（ADRを含む）を作成・変更・レビューする時 | `agent-basics:writing-standards/references/document-types.md` | | | |
| 新しい概念名または識別子を導入する時と、既存の名前で複数の主体・プロジェクト・変更前後の対象を説明する時 | `references/referent-table.md` | | | |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `references/defined-names.md` | | | |
| 文章を書く時と表記を修正する時（他の行より先に読む） | `references/notation-rules.md` | 操作 | `body-writing` | |
| 表記チェックの対象・実行方法を選ぶ前、実行する前と診断へ対応する前 | `references/notation-checks.md` | | | |

## コードの編集時に読む資料

コードの編集やコードレビュー（変更差分の確認）に着手する前に、次の資料を読む。

- `references/writing.md`
- `references/writing-common.md`
- `references/writing-readers.md`
- `references/writing-genres.md`
- `references/writing-maintenance.md`
同じ時点で、以降の技術別・工程別の表で該当する資料を読む。
成果物として残さない使い捨ての一時コードは、言語別の必須読込の対象外とする。
安全性、認可、データ保全の制約は維持し、成果物へ取り込む段階で該当する品質基準を適用する。

| 時点または条件（対象の拡張子・ファイル名・パス・依存名） | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| `py` | `agent-basics:writing-standards/references/python.md` | 操作 | `path:*.py` | |
| `ts`・`tsx` | `references/typescript.md` | 操作 | `path:*.ts`、`path:*.tsx` | |
| `rs` | `references/rust.md` | 操作 | `path:*.rs` | |
| `cs` | `references/csharp.md` | 操作 | `path:*.cs` | |
| `sh`・`bash`・`sh.tmpl` | `references/bash.md` | 操作 | `path:*.sh`、`path:*.bash`、`path:*.sh.tmpl` | |
| `ps1`・`ps1.tmpl`・`psm1`・`psd1` | `references/powershell.md` | 操作 | `path:*.ps1`、`path:*.ps1.tmpl`、`path:*.psm1`、`path:*.psd1` | |
| `cmd`・`bat` | `references/windows-batch.md` | 操作 | `path:*.cmd`、`path:*.bat` | |
| `Dockerfile` | `references/dockerfiles.md` | 操作 | `path:*Dockerfile` | |
| `.github/workflows/*.yaml`・`.github/workflows/*.yml` | `references/github-actions.md` | 操作 | `path:*/.github/workflows/*.yaml`、`path:*/.github/workflows/*.yml` | |
| `tailwindcss`（v4系） | `references/tailwindcss.md` | 操作 | `dependency:tailwindcss`（実コードまたは依存定義の操作） | |
| `alpinejs`（v3系。`<script>`読み込みを含む） | `references/alpinejs.md` | 操作 | `dependency:alpinejs`（実コードまたは依存定義の操作） | |
| `@playwright/test`・`playwright`（Playwright Test v1系とPythonバインディング） | `references/playwright.md` | 操作 | `dependency:playwright`（実コードまたは依存定義の操作） | |
| `sqlalchemy`（2.0系） | `references/sqlalchemy.md` | 操作 | `dependency:sqlalchemy`（実コードまたは依存定義の操作） | |
| `drizzle-orm`・`drizzle-kit`（0.x系） | `references/drizzle.md` | 操作 | `dependency:drizzle`（実コードまたは依存定義の操作） | |
| `svelte`・`@sveltejs/kit`（Svelte 5・SvelteKit 2〜3） | `references/svelte.md` | 操作 | `dependency:svelte`（実コードまたは依存定義の操作） | |
| `react`（関数コンポーネントとHooks） | `references/react.md` | 操作 | `dependency:react`（実コードまたは依存定義の操作） | |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時（作業項目の起草や対話でその案を示す時点を含む） | `references/design-time.md` | | | |
| プログラムの出力メッセージ（エラー、警告、ログ、CLIの出力やヘルプなど）を新設・変更する時と、その変更をレビューする時 | `references/program-messages.md` | 判断 | `skill:writing-standards` | |
| コードを編集する時と、依存の追加・更新をする時 | `references/implementation-time.md` | | | |
| 設計判断を確定する時、計画と実装を同じエージェントが続けて実施する時、コードレビューの時 | `references/design-heuristics-screening.md`、`references/design-heuristics-necessity.md`、`references/design-heuristics-mechanisms.md`、`references/design-heuristics-responsibility.md`、`references/design-heuristics-proportionality.md`、`references/design-heuristics-decisions.md`、`references/design-heuristics-quality.md` | | | |
| MCPサーバーまたはホスト登録ツール（Function hooksなど）の説明、引数、応答を設計、実装、変更、レビューする時 | `references/mcp-server-design.md` | | | |
| テスト設計・検証手法を選ぶ時、テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `references/testing.md` | | | |
| Pythonの静的解析（ruff・pylint・pyright・mypyなど）の診断へ対処する時と、抑制を追加する時 | `references/python-analysis.md` | | | |
| 文字エンコーディングを扱う時（日本語環境・ZIPファイル・Unicode正規化など） | `references/encoding.md` | | | |
| 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボードなど）の作成・修正時 | `references/independent-html.md` | | | |
| エンドユーザーが操作する画面をHTML、CSS、JavaScriptで実装またはレビューする時 | `references/ui-ux-web-rules.md` | | | |
| hookスクリプトと機械チェックスクリプトを書く時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `skill:writing-standards` | `same-document` |

## エージェント向け文書の編集時に読む資料

エージェント向け文書の種類や行う操作に応じて、次の読込表を適用する。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| エージェント向け文書（AGENTS.md、CLAUDE.md、ルール、SKILL.md、サブエージェント定義、referencesなど）の編集前 | `references/writing.md`、`references/writing-common.md`、`references/writing-readers.md`、`references/writing-genres.md`、`references/writing-maintenance.md`、`references/agent-documents-basics.md` | 操作 | `agent-document-writing` | |
| スキル編集 | `references/agent-skills.md` | | | |
| スキル編集 | `references/host-official-references.md` | | | |
| エージェント向け文書の置き場所を選ぶ時 | `references/agent-documents-basics.md` | | | |
| 既存の規範・基準・手順で対応済み（被覆済み）かを判定する時（作業項目の起草・計画・レビュー・再発防止策の採否を含む） | `references/agent-documents-basics-ownership.md` | | | |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `references/sub-agents.md` | | | |
| hookスクリプトの新設・変更（入出力、matcher、環境変数） | `references/claude-hooks.md` | | | |
| hookスクリプトの新設・変更（入出力、matcher、環境変数） | `references/host-official-references.md` | | | |
| 遮断・警告を返すhookの判定を新設・変更する時と、hookを置くか、遮断と警告のどちらにするかを判定する時 | `references/claude-hooks-block-warn.md` | | | |
| hookの出力フィールドを選ぶ時と、`PermissionRequest`・`UserPromptSubmit`のhookを新設・変更する時 | `references/claude-hooks-output.md` | | | |
| `Stop`・`SubagentStop`のhookと、終了工程の証拠を新設・変更する時 | `references/claude-hooks-stop.md` | | | |
| hookなどの生成元がエージェントへ直接渡す通知本文・`fix`・次の操作・圧縮時に会話へ入れる本文を新設・変更する時（受信側、命令形、具体的な操作、言語と標識の書き方を含む） | `references/claude-hooks-messages.md` | | | |
| auto modeのカスタムルール編集 | `references/auto-mode.md` | | | |
| auto modeのカスタムルール編集 | `references/host-official-references.md` | | | |
| 機械チェックスクリプトの新設・改修 | `references/check-script-design.md` | | | |
| 規範追記を含む変更要求・方針・作業項目・計画を起草し採否を判断する時、エージェント向け文書へ規定を追記する時、および文書の記述量を管理する時 | `references/agent-documents-additions.md` | | | |

## 導入と適用

本プラグインは成果物の品質基準を定めている。
各読込表の条件に従って必要な資料を読み、同じバージョンの資料をすでに読んでいる場合はその内容を再利用する。
作業の認可や委譲、記録の保存、工程管理については、対象プロジェクトで採用されている手順に従う。
