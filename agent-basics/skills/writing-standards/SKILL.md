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
  GitLab CI設定の編集・確認・キーワード仕様・典型パターン・lint実行方法の参照時、
  pytilpackのモジュール構成・API・extras・CLIの参照時と、`import pytilpack`を含むコードの読み書き時も起動する。
  これらを除く理解のためのコード読取では起動不要。
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

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../review-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/review-standards/SKILL.md` |

## 成果物種別ごとの必読資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 既存ファイルを編集・上書きする時、スクリプトで複数ファイルを機械変換する時 | `references/file-editing.md` | 操作 | `existing-file-edit` | |
| 成果物、報告・回答、委譲プロンプトへ書く事実主張を調査する時 | `references/investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` | |
| Xの投稿本文・日時・添付写真を資料として読む前 | `references/x-posts.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/x-posts.md` | |
| ホスト機能の可否・入出力契約を調べる時 | `references/host-official-references.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/host-official-references.md` | |
| 計画の起草、実現方式の確定、実装の完了前と実行レビューで、変更後の設計品質を評価する時（エージェント向け文書だけの変更を含む）と、外部資料（論文、ベンチマーク、他プロジェクトの事例など）に基づく改善案を提案・評価する時 | `references/design-heuristics.md`の「設計案の予備選別」から「設計品質の劣化確認」まで（各節の適用条件を保つ） | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-heuristics.md#「設計案の予備選別」から「設計品質の劣化確認」まで` | |
| 技術記事・操作手順などの技術文書を書く時 | `references/writing.md`の「技術文書」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「技術文書」` | |
| 業務・仕様文書を書く時（変更説明、障害報告、提案書、議事録、規程、作業項目などを含む） | `references/writing.md`の「業務・仕様文書」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「業務・仕様文書」` | |
| 体験を述べる文章を書く時 | `references/writing.md`の「体験を述べる文章」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「体験を述べる文章」` | |
| スライドを書く時 | `references/writing.md`の「スライド」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「スライド」` | |
| 紹介・宣伝文を書く時 | `references/writing.md`の「紹介・宣伝文」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「紹介・宣伝文」` | |
| メール・チャットを書く時 | `references/writing.md`の「メール・チャット」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「メール・チャット」` | |
| 論文・調査報告を書く時 | `references/writing.md`の「論文・調査報告」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「論文・調査報告」` | |
| 脚本・台本を書く時 | `references/writing.md`の「脚本・台本」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「脚本・台本」` | |
| ユーザーの入力素材を整理して取り込む時 | `references/writing.md`の「ユーザー入力素材の取扱い」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「ユーザー入力素材の取扱い」` | |
| 配布物がエンドユーザーの環境でパッケージマネージャーへ渡す版指定（`uvx --from`の引数、`pyproject.toml`の`dependencies`、`uv run --with`の引数、PEP 723の`dependencies`など）を変える作業の起草、計画、実装、レビューの時 | `references/dependency-management.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/dependency-management.md` | |
| エンドユーザーが操作する画面の新設・変更、その計画・作業項目の起草またはレビューの時 | `references/ui-ux.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/ui-ux.md` | |
| エンドユーザーが操作する画面の新設・変更、その計画・作業項目の起草またはレビューで、フォーム、一覧・データ表、検索、通知、モーダル・パネル、AI機能、同意・解約または多言語表示を含む時 | `references/ui-ux-patterns.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/ui-ux-patterns.md` | |
| ユーザーが法令上の義務やプライバシーの確認を依頼した時と、不特定のエンドユーザーへ公開・提供するWebサービスやアプリで、第三者サーバーからのリソース読み込みや解析・広告タグ、操作や入力の記録、宣伝メールの一斉送信、定期課金、エンドユーザーの投稿の公開または会員登録を新設・変更する時（その計画・作業項目の起草またはレビューを含む）。自分用ツール、社内システム、ユーザーが自分で開く単体HTML成果物は、確認の依頼と社外の不特定のエンドユーザーへ公開範囲を広げる変更の場合だけ該当する | `references/public-service-obligations.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/public-service-obligations.md` | |
| 起動時 | `references/quality-principles.md` | 起動時 | `skill:writing-standards` | |
| 人間が読む文章（ドキュメント、コメント、作業項目の本文など）を書く時 | `references/writing.md`の「文章の共通規定」「読み手別の追加注意点」「構成・構造」「日本語の書き方」「改訂・保守」 | 起動時 | `skill:writing-standards` | |
| コード・テストコードを書く時 | 「コードの編集時に読む資料」の各行 | 判断 | `skill:writing-standards` | `same-document` |
| エージェント向け文書を書く時 | 「エージェント向け文書の編集時に読む資料」の各行 | 判断 | `skill:writing-standards` | `same-document` |
| Claude CodeでEditを使う時と、Writeで既存ファイルを上書きする前 | `references/file-editing-claude.md` | 操作 | `claude-existing-file-edit` | |

領域別の資料を選ぶときは、依頼が求める結果と変更対象のインターフェースや仕様から該当する領域を特定する。ここでの特定は読むべき資料を決めるためのものであり、具体的な設計案の比較や採用は該当節を読んだ後に行う。入力内容だけから領域を判断できない場合は、対象ファイルの実物を読み取って確認してから該当節に進む。文書の構成のみを判断する場合のように領域の条件に当てはまらないときは、共通の設計基準を用いる。資料の取得には通常の読取操作を使い、読込表で指定された節を読んでから設計の検討に戻る。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 並行処理・排他制御を設計・変更・レビューする前 | `references/design-runtime.md`の「並行処理・排他制御の設計観点」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-runtime.md#「並行処理・排他制御の設計観点」` | |
| 検索・一覧画面を設計・変更・レビューする前 | `references/design-display.md`の「検索・一覧画面の設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-display.md#「検索・一覧画面の設計」` | |
| 閾値と表示段階を設計・変更・レビューする前 | `references/design-display.md`の「閾値と表示段階の設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-display.md#「閾値と表示段階の設計」` | |
| キャッシュを設計・変更・レビューする前 | `references/design-runtime.md`の「キャッシュ設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-runtime.md#「キャッシュ設計」` | |
| 非同期ジョブ・リトライを設計・変更・レビューする前 | `references/design-runtime.md`の「非同期ジョブ・リトライ設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-runtime.md#「非同期ジョブ・リトライ設計」` | |
| DBスキーマ・マイグレーションを設計・変更・レビューする前 | `references/design-data-interfaces.md`の「DBスキーマ・マイグレーション設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-data-interfaces.md#「DBスキーマ・マイグレーション設計」` | |
| APIを設計・変更・レビューする前 | `references/design-data-interfaces.md`の「API設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-data-interfaces.md#「API設計」` | |
| 認証・認可を設計・変更・レビューする前 | `references/design-data-interfaces.md`の「認証・認可設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-data-interfaces.md#「認証・認可設計」` | |
| ファイルアップロードを設計・変更・レビューする前 | `references/design-data-interfaces.md`の「ファイルアップロード設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-data-interfaces.md#「ファイルアップロード設計」` | |
| 状態を変える多段手順の工程順を設計・変更・レビューする前 | `references/design-runtime.md`の「状態を変える多段手順の工程順」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-runtime.md#「状態を変える多段手順の工程順」` | |
| テストの網羅範囲の管理方法を設計・変更・レビューする前 | `references/test-coverage-ledger.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/test-coverage-ledger.md` | |
| 画面を操作する探索的な試行の実施要否を決める前 | `references/testing.md`の「検証単位の選定」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「検証単位の選定」` | |
| DBトランザクションを設計・変更・レビューする前 | `references/design-data-interfaces.md`の「DBトランザクション設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-data-interfaces.md#「DBトランザクション設計」` | |
| クエリ性能を設計・変更・レビューする前 | `references/design-data-interfaces.md`の「クエリ性能設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-data-interfaces.md#「クエリ性能設計」` | |
| DB更新と外部送信の整合性を設計・変更・レビューする前 | `references/design-data-interfaces.md`の「DB更新と外部送信の整合性設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-data-interfaces.md#「DB更新と外部送信の整合性設計」` | |
| Webhook受信を設計・変更・レビューする前 | `references/design-data-interfaces.md`の「Webhook受信設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-data-interfaces.md#「Webhook受信設計」` | |
| 常駐サービスを設計・変更・レビューする前 | `references/design-runtime.md`の「常駐サービス設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-runtime.md#「常駐サービス設計」` | |
| 負荷制御と外部呼び出しを設計・変更・レビューする前 | `references/design-runtime.md`の「負荷制御と外部呼び出しの設計」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-runtime.md#「負荷制御と外部呼び出しの設計」` | |

## 技術資料の確認・参照・読取

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| `.gitlab-ci.yml`の編集・確認時と、GitLab CIのキーワード仕様・典型パターン・lint実行方法を参照する時 | `references/gitlab-ci.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/gitlab-ci.md` |
| `import pytilpack`を含むコードを読み書きする時と、pytilpackのモジュール構成・API・extras・CLIの使い方を参照する時 | `references/pytilpack.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/pytilpack.md` |

## 文章の作成時に読む資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| textlintなど文章チェックの診断へ対応する時 | `references/textlint-violations.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/textlint-violations.md` | |
| 文章の書き直しを委ねる時 | `references/textlint-violations.md`の「文の書き直しの委譲と検収」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/textlint-violations.md#「文の書き直しの委譲と検収」` | |
| 独立したレビューを経ずに成果物を完成させる時（再レビューを省く修正、独立したレビューを起動しない修正経路を含む）と、レビュー限定基準の指摘を受けて採否を判断する時 | `references/review-only-criteria.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/review-only-criteria.md` | |
| lint設定の緩和・無効化・除外指定を追加する時 | `references/lint-relax-criteria.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/lint-relax-criteria.md` | |
| 文の口調を書き換える処置を選ぶ時（口語表現の警告、textlintのAI文体の指摘を含む）。正しい例と説明から、元の意味と条件を保つ書き方を選ぶ | `references/tone-examples.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/tone-examples.md` | |
| 文の口調を書き換える処置を選ぶ時（口語表現の警告、textlintのAI文体の指摘を含む）。正しい例と説明から、元の意味と条件を保つ書き方を選ぶ | `references/tone-examples-llm-tone.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/tone-examples-llm-tone.md` | |
| 人間向け文書の役割と残す内容を選ぶ時 | `references/document-types.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/document-types.md` | |
| 新しい概念名または識別子を導入する時 | `references/referent-table.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/referent-table.md` | |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `references/defined-names.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/defined-names.md` | |
| 文章を書く時と表記をチェックする時（他の行より先に読む） | `references/notation-rules.md` | 起動時 | `skill:writing-standards` | |

## コードの編集時に読む資料

コードの編集やコードレビュー（変更差分の確認）に着手する前に、`references/writing.md`の「文章の共通規定」「読み手別の追加注意点」「構成・構造」「日本語の書き方」「改訂・保守」と、以降の技術別・工程別の表で該当する資料を読む。

| 時点または条件（対象の拡張子・ファイル名・パス・依存名） | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| `py` | `references/python.md`の「言語スタイル」「テストコード（pytest）」「Python新構文と導入バージョン」 | 操作 | `path:*.py` | |
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
| `svelte`・`@sveltejs/kit`（Svelte 5・SvelteKit 2） | `references/svelte.md` | 操作 | `dependency:svelte`（実コードまたは依存定義の操作） | |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `references/design-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time.md` | |
| コードを編集する時と、依存の追加・更新をする時 | `references/implementation-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time.md` | |
| 設計判断を確定する時、計画と実装を同じエージェントが続けて実施する時、コードレビューの時 | `references/design-heuristics.md`の「設計案の予備選別」から「設計品質の劣化確認」まで（各節の適用条件を保つ） | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-heuristics.md#「設計案の予備選別」から「設計品質の劣化確認」まで` | |
| 依存の追加・更新、またはPythonの対応版を追加する時（CI・開発環境の版指定変更を含む） | `references/dependency-management.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/dependency-management.md` | |
| MCPサーバーまたはホスト登録ツール（Function hooksなど）の説明、引数、応答を設計、実装、変更、レビューする時 | `references/mcp-server-design.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/mcp-server-design.md` | |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `references/testing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing.md` | |
| Pythonの静的解析（ruff・pylint・pyright・mypyなど）の診断へ対処する時と、抑制を追加する時 | `references/python.md`の「静的解析の誤検出と抑制」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/python.md#「静的解析の誤検出と抑制」` | |
| 文字エンコーディングを扱う時（日本語環境・ZIPファイル・Unicode正規化など） | `references/encoding.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/encoding.md` | |
| 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボードなど）の作成・修正時 | `references/independent-html.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/independent-html.md` | |
| エンドユーザーが操作する画面をHTML、CSS、JavaScriptで実装またはレビューする時 | `references/ui-ux-web-rules.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/ui-ux-web-rules.md` | |
| hookスクリプトと機械チェックスクリプトを書く時 | 「エージェント向け文書の編集時に読む資料」の表のうち、hookおよび機械チェックスクリプトに関する行で条件が成立する行の資料 | 判断 | `skill:writing-standards` | `same-document` |

## エージェント向け文書の編集時に読む資料

エージェント向け文書の種類や行う操作に応じて、次の読込表を適用する。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| エージェント向け文書（AGENTS.md、CLAUDE.md、ルール、SKILL.md、サブエージェント定義、referencesなど）の編集前 | `references/writing.md`の「文章の共通規定」「読み手別の追加注意点」「構成・構造」「日本語の書き方」「改訂・保守」と`references/agent-documents-basics.md` | 操作 | `agent-document-writing` | |
| スキル編集 | `references/agent-skills.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills.md` | |
| スキル編集 | `references/host-official-references.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/host-official-references.md` | |
| エージェント向け文書の置き場所を選ぶ時 | `references/agent-documents-basics.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md` | |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `references/sub-agents.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/sub-agents.md` | |
| hookスクリプトの新設・変更（入出力、matcher、環境変数） | `references/claude-hooks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks.md` | |
| hookスクリプトの新設・変更（入出力、matcher、環境変数） | `references/host-official-references.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/host-official-references.md` | |
| 遮断・警告を返すhookの判定を新設・変更する時と、hookを置くか、遮断と警告のどちらにするかを判定する時 | `references/claude-hooks-block-warn.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md` | |
| hookの出力フィールドを選ぶ時と、`PermissionRequest`・`UserPromptSubmit`のhookを新設・変更する時 | `references/claude-hooks-output.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-output.md` | |
| `Stop`・`SubagentStop`のhookと、終了工程の証拠を新設・変更する時 | `references/claude-hooks-stop.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-stop.md` | |
| 遮断・警告・定型の通知を出力するhookを新設・変更する時と、hookなどの生成元がエージェントへ直接渡す本文（記述言語と出所を示す標識）を新設・改訂する時 | `references/claude-hooks-messages.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md` | |
| auto modeのカスタムルール編集 | `references/auto-mode.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/auto-mode.md` | |
| auto modeのカスタムルール編集 | `references/host-official-references.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/host-official-references.md` | |
| 機械チェックスクリプトの新設・改修 | `references/check-script-design.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/check-script-design.md` | |
| エージェント向け文書へ新しい規定を追記する時と、文書の記述量を管理する時 | `references/agent-documents-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions.md` | |

## 導入と適用

本プラグインは成果物の品質基準を定めている。各読込表の条件に従って必要な資料を読み、同じバージョンの資料をすでに読んでいる場合はその内容を再利用する。作業の認可や委譲、記録の保存、工程管理については、対象プロジェクトで採用されている手順に従う。
