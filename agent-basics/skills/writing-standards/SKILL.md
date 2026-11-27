---
name: writing-standards
user-invocable: false
description: >
  ドキュメント・コメント・コード・テストコード・エージェント向け文書
  （`AGENTS.md`・`CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時と、人に届ける通常の返答・質問を書く前に呼び出す。
  作業項目の本文の起草時、成果物へ書く事実主張の裏付け調査時、ホスト機能の可否・入出力契約の調査時、
  記録の集計・分析時、規範・基準・手順の妥当性や置き場所を確認する時、外部資料を根拠に使う前にも呼び出す。
  GitLab CI設定の編集・確認・キーワード仕様・lint実行方法の参照時、
  pytilpackのモジュール構成・API・extras・CLIの参照時、`import pytilpack`を含むコードの読み書き時と、
  pytilpackへ依存するプロジェクトでPythonの汎用的な処理を新しく実装する時も起動する。
  これらを除く理解のためのコード読取では起動不要。
# 編集時の注意点:
# 著者向けの品質基準だけを扱い、レビュー担当とレビューイーの判断指針はreview-standardsが定める。
# 本体は目的、手順、内容で書いた条件から本文を選ぶ索引だけを持ち、詳細は主題ごとに1つの本文（references/）へ置く。
# 本文から別の本文の全文を読ませる連鎖を作らない。組み合わせは索引が選ぶ。
---

# 成果物の品質基準

成果物を作る担当が利用場面に合う品質基準を選び、ユーザーが求めた結果を実際に使える形で届ける。
文書、コード、テスト、エージェント向け文書を作る者が、成果物の品質を判断する基準である。
判断の土台は9つの原則（`references/quality-principles.md`）で、各本文は原則を具体的な成果物、読者、技術へ適用したものである。
レビューと指摘の採否の基準は`agent-basics:review-standards`が定め、作業の認可、委譲、記録、工程は対象プロジェクトの手順に従う。
名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 判断の初動

- 原要求から、成果物を使う主体、利用場面、必要な最終結果、認可の範囲を確定し、成果物や提案をその結果へ対応付ける。
- 結果を分ける軸（要求、主体、操作、状態、入力、環境、生成側と利用側など）と値を定め、成立する組合せと期待結果を対応付ける。
  有限の対象は全件を挙げ、巨大な組合せは同値クラス、到達不能な条件、リスクで縮め、範囲と根拠と残る未確認を示す。
  単純な変更では短い記録でよく、組合せの整理を全組合せの試験の義務にしない。
- 処理の受付と完了を区別し、終了状態を決める主体の出力で確かめるまでは未確認として扱う。

## 作業を始める

- コードを書く: 最初の編集前に `references/implementation-time.md` を使う。
  説明は、名前・型・コード・検査と必要なコメントをQCDで選び、コードの言い換えは書かない。
  判断や制約の根拠を残す条件は、同資料「コメントの記述粒度」に従う。
  Pythonの初回編集前には、例外の有無にかかわらず `references/python.md`「import・型・docstring」も使う。
  コードを書かない場合はこの初動は不要。
- 文章を書く: 最初に `references/writing.md` を使い、読者・用途・次の行動を決める。
  一時的な返答ならここまでで始め、成果物を作る場合は表記・文書の種類・エージェント向けの条件に該当する資料を「文章」「エージェント向け文書」から選ぶ。

本文の取得と再利用は`references/read-method.md`に従う。
複数の仕事を始める時は、それぞれを始める時点で適用する。
個別の言語・操作・判断の条件に当たらなければ、その資料は取得しない。

## 手順

成果物を作らない通常の返答・質問では、`references/writing.md` の該当箇所に従い、取得済みの版を再利用する。
成果物を作る時は、次の手順を行う。

1. 本書「判断の初動」を行う。
2. 本書「索引」から、成果物と、書く内容・行う操作に当たる行を全て選び、指定の資料を全文読む。
   条件は文書全体の種類ではなく、その部分に書く内容で判定する（手続き文書の中の利点を伝える節など）。
3. 判断に関わる原則の本文を読み、原則と本文を合わせて適用する。
4. 成果物を作る。
5. 導入されている検査（formatter、linter、テスト、文章チェック）を実行し、診断に従う。
   ファイルの変更や検査の実行ができない環境では、原稿と未実行の検査を未検証として返し、変更と保存は実行できる環境で続ける。
6. 独立したレビューを経ずに完成させる時は、`references/review-only-criteria.md`で自己点検する。
7. 報告に、適用した本文と原則の名前を挙げる。

## 索引

条件が成立したら、判断や操作の前に資料の全文を読む。

### 共通

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 起動時 | `references/quality-principles.md` |
| 成果物、報告、回答、委譲の依頼文へ事実の主張を書く時と、その裏付けを調べる時、実行・測定・検証の結果を報告・判定する時（エージェントCLIや共有状態を扱うツールを検証で起動する時、性能を測る時を含む） | `references/investigation.md` |
| 成果物、作業記録、WI、報告に時刻（現在時刻、過去の出来事の時刻、将来の予定時刻）を書く前 | `references/quality-principles-facts.md` |
| 観測で前提が崩れて次の確認を選ぶ時、経験を保存・再利用する時、観測に基づいてエージェント向け文書の技術的な条文を新設・改訂する時、プロジェクトの文書と既存コードの規約が違う時 | `references/investigation.md` |
| X投稿などの外部資料の本文・日時・添付画像を取得・確認する前と、外部資料を成果物の根拠に使う前 | `agent-basics:fetch-sources/SKILL.md` |
| 成果物や提案のレビュー、指摘の採否を判断する前 | `agent-basics:review-standards/SKILL.md` |
| 独立したレビューを経ずに成果物を完成させる時（再レビューを省く修正、独立したレビューを起動しない手順を含む）、初回レビューで作成側の基準を詳しく点検する時、レビュー限定基準の指摘の採否を決める時 | `references/review-only-criteria.md` |
| 既存ファイルを編集・上書きする時、スクリプトで複数ファイルを機械変換する時 | `references/file-editing.md` |
| Claude CodeでEditを使う時と、Writeで既存ファイルを上書きする前 | `references/file-editing.md` |
| 文字コード・改行・BOMを扱う時（入出力、ZIPのファイル名、Unicodeの正規化と比較など）、識別子やアカウントを大文字小文字を区別せず比較する処理を実装・レビューする時 | `references/encoding.md` |
| CIから公開までの実行順、必須検証、結果の再利用を設計する時 | `references/github-actions.md` |

### 文章

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 人間が読む成果物（文書、コメント、作業項目、委譲の依頼、報告、調査結果の回答、コミットメッセージなど）を書く時と直す時、日本語やMarkdownの表記を直す時、多数の箇所の語句を一括置換する時 | `references/writing.md`、`references/notation-rules.md` |
| 文書の一部の節でも次の内容を書く時（文書の種類を問わない）: 読者に利点や訴求を伝える記述（手続き文書や技術文書の「メリット」節、導入の勧め、紹介・宣伝文を含む）、操作手順や技術の説明、読者に判断・決定・行動を求める記述（変更の説明、障害報告、提案、議事録、規程、作業項目）、書き手の体験や感想、スライド、特定の相手へ送るメール・チャット・依頼文、調査や実験の結果、脚本・台本 | `references/writing-genres.md` |
| ユーザーの入力素材（背景を説明しながらの指示、原文、引用）を文章へ取り込む時と、原文を書き直す時 | `references/writing-genres.md` |
| 情報をどの文書へ書き分けるかを決める時、文書の種類と役割を選ぶ時、設計の記録（ADRを含む）を作成・変更・レビューする時 | `references/document-types.md` |
| ADRを書く・改稿する・レビューする時 | `references/adr.md` |
| 造語や独自の言い換えを避ける時、名前を付ける時（新しい概念名、識別子、工程・担当の名前、画面の表示名）、既存の対象を名前で指す時、同名・同種の対象が複数の主体・プロジェクト・変更前後にまたがる記述をする時 | `references/defined-names.md` |
| 文章チェック（textlint、markdownlint、口語表現、ダッシュ記号、typos）の対象・実行方法を選ぶ時、実行する時、診断へ対応する時、文の書き直しを委ねる時と受け取る時 | `references/textlint-violations.md` |
| 文の口調を書き換える処置を選ぶ時（口語表現の警告、textlintのAI文体の指摘を含む） | `references/tone-examples.md` |

### 設計

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 設計案を選ぶ・比べる・採否を決める時、構成要素・機構・例外・フォールバック・処理を止める判定を加えるか変える時、修正対象の存在意義や責務の配置を点検する時、計画の起草・実装の完了前・実行レビューで変更後の設計品質や累積したファイル構成を評価する時（エージェント向け文書だけの変更を含む）、外部資料（論文、ベンチマーク、他プロジェクトの事例など）に基づく改善案を提案・評価する時 | `references/design-heuristics.md` |
| 計画ファイルを作成する時と、公開インターフェース（API、CLIのオプション、設定キーと書式、出力形式、Webhook）、エンドユーザー向けのメッセージ、永続データの形式・保持・移行・削除を新設・変更する時（作業項目の起草や対話でその案を示す時点を含む） | `references/design-data-interfaces.md` |
| 認証・認可、ファイルアップロード、DBのスキーマ・トランザクション・クエリ、DBの更新と外部送信や外部資源との組合せを設計・変更・レビューする時、状態や業務ルールを持つ機能を新設・変更する時と、その不変条件と保証箇所をレビューする時 | `references/design-data-interfaces.md` |
| 並行処理と排他、イベントループ上の処理、複数の主体が同じ保存先へ書く構成、非同期ジョブ・再試行・定期実行・常駐プロセスを設計・変更・レビューする時と、再試行を足す・変える時 | `references/design-runtime.md` |
| インターネットへ公開するサービスを設計・配置する時（コード変更の有無を問わない）と、公開用の証明書・DNS・リバースプロキシを設定する時 | `references/design-runtime.md` |
| 入力の大きさ・件数・長さ・同時実行数などの処理量の上限を決める・見直す時（コードを書かない設計・計画・レビューを含む） | `references/implementation-time.md` |
| キャッシュ、長時間接続、常駐サービスと配下の停止・再開・切替、負荷制御と外部呼び出し、負荷・データ・外部システムの条件に依存する技術仕様や非機能要件、後の工程が使う前提を変える工程を含む多段の手順を設計・変更・レビューする時 | `references/design-runtime.md` |
| エンドユーザーが操作する画面（Web以外を含む）の新設・変更、その計画・作業項目の起草、実描画での検証、ユーザビリティのレビュー | `references/ui-ux.md`、`references/ui-ux-topics.md` |
| 検索・一覧の方式（一致の方式、索引、ページ送り、総件数、並び替え）を設計・変更する時、値に応じて色・状態・通知を切り替える境界値を決める時（画面を伴わない一覧取得のAPIを含む） | `references/ui-ux.md`、`references/ui-ux-topics.md` |
| エンドユーザー向けの画面をHTML、CSS、クライアント側JavaScriptで実装・変更・レビューする時、ユーザーへ単体で渡すHTML（レポート、ダッシュボード、可視化など）を作る・直す時 | `references/ui-ux-web-rules.md` |
| Web性能を改善・設計・レビューする時 | `references/ui-ux.md`、`references/ui-ux-topics.md`、`references/ui-ux-web-rules.md` |

### 実装とテスト

| 時点または条件 | 全文読む資料 |
| --- | --- |
| プログラムの出力メッセージ（エラー、警告、ログ、CLIの出力やヘルプなど）を新設・変更する時と、その変更をレビューする時 | `references/program-messages.md` |
| テスト設計・検証手法を選ぶ時、テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `references/testing.md`、`references/testing-design.md` |
| テストの網羅範囲の管理方法を設計・変更・レビューする時、画面を操作する探索的な試行の要否を決める時、状態遷移・並行処理・認可の検証を設計する時 | `references/testing.md`、`references/testing-design.md` |
| 依存パッケージ、コンテナーイメージ、言語処理系、CI・自動ビルドのツールやactionのバージョン指定を選ぶ・変える作業の起草、計画、実装、レビューの時（配布物が渡す`uvx --from`、`dependencies`、`uv run --with`、PEP 723のバージョン指定、Python対応バージョンの追加、pnpmの導入とバージョンの更新を含む） | `references/dependency-management.md` |
| 自分の配布物のバージョン番号を決める・上げる・レビューする時と、依存先のバージョン番号から互換性を判断する時 | `references/versioning.md` |
| MCPサーバーまたはホスト登録ツール（Function hooksなど）の説明、引数、応答を設計、実装、変更、レビューする時 | `references/mcp-server-design.md` |

### 技術別

| 時点または条件（対象の拡張子・ファイル名・パス・依存名） | 全文読む資料 |
| --- | --- |
| `py`（Pythonのコードとテスト、静的解析の診断への対処と抑制を含む） | `references/python.md` |
| `ts`・`tsx` | `references/typescript.md` |
| `rs` | `references/rust.md` |
| `cs` | `references/csharp.md` |
| `sh`・`bash`・`sh.tmpl` | `references/bash.md` |
| `ps1`・`ps1.tmpl`・`psm1`・`psd1`、PowerShellからCOMで共有Office文書を編集する時 | `references/powershell.md` |
| `cmd`・`bat` | `references/windows-batch.md` |
| `Dockerfile` | `references/dockerfiles.md` |
| `.github/workflows/*.yaml`・`.github/workflows/*.yml` | `references/github-actions.md` |
| `tailwindcss`（v4系） | `references/tailwindcss.md` |
| `alpinejs`（v3系。`<script>`読み込みを含む） | `references/alpinejs.md` |
| `@playwright/test`・`playwright`（Playwright Test v1系とPythonバインディング） | `references/playwright.md` |
| `sqlalchemy`（2.0系） | `references/sqlalchemy.md` |
| `drizzle-orm`・`drizzle-kit`（0.x系） | `references/drizzle.md` |
| `svelte`・`@sveltejs/kit`（Svelte 5・SvelteKit 2〜3） | `references/svelte.md` |
| `react`（関数コンポーネントとHooks） | `references/react.md` |
| `.gitlab-ci.yml`の編集・確認時と、GitLab CIのキーワード仕様・典型パターン・lint実行方法を参照する時、私設GitLabで`glab`がTLS証明書の検証エラーを返した時 | `references/gitlab-ci.md` |
| `import pytilpack`を含むコードを読み書きする時、pytilpackのモジュール構成・API・extras・CLIの使い方を参照する時と、pytilpackへ依存するプロジェクトでPythonの汎用的な処理を新しく実装する前 | `references/pytilpack.md` |

### エージェント向け文書

| 時点または条件 | 全文読む資料 |
| --- | --- |
| エージェント向け文書（`AGENTS.md`、`CLAUDE.md`、ルール、`SKILL.md`、サブエージェント定義、references、役割文書など）を新設・編集する前 | `references/writing.md`、`references/agent-documents-basics.md` |
| 規範を追記・新設・移動する置き場所を決める時、既存の規範・基準・手順で対応済み（被覆済み）かを判定する時（作業項目の起草・計画・レビュー・再発防止策の採否を含む） | `references/agent-documents-basics.md` |
| 規範の不足や誤適用に対処して規定を足す・改める時（規範の追記を含む変更要求・作業項目・計画の起草と採否を含む）、文書の記述量を点検・削減する時 | `references/agent-documents-additions.md` |
| スキル（`SKILL.md`、frontmatter、references）を新設・編集する時 | `references/agent-skills.md` |
| サブエージェント定義、委譲元の契約文書、委譲先の手順書を新設・編集する時と、サブエージェントが関わる手順を作る・改める時 | `references/sub-agents.md` |
| hookスクリプトを新設・変更する時（入出力、matcher、環境変数、出力フィールド、`PermissionRequest`・`UserPromptSubmit`・`Stop`・`SubagentStop`、Codexとの差、管理環境での実行）、ホスト機能の可否・入出力契約を調べる時、スキル編集で公式資料を確かめる時、子のClaude Codeへ環境変数を渡す時、モデルの更新で指示文を点検する時、auto modeのカスタムルールを書く・直す時 | `references/claude-hooks.md` |
| hook、検査、CLIの判定で遮断・警告・通知を置くか、どちらにするかを決める時、遮断・警告を返すhookの判定を新設・変更する時、hookなどの生成元がエージェントへ直接渡す通知本文・`fix`・次の操作・言語・標識・圧縮時に会話へ入れる本文を新設・変更・レビューする時 | `references/claude-hooks-block-warn.md`、`references/program-messages.md` |
| 機械チェックスクリプトを新設・改修する時 | `references/check-script-design.md`、`references/program-messages.md` |

### 特別な成果物

| 時点または条件 | 全文読む資料 |
| --- | --- |
| ロゴ、ワードマーク、ブランドマークとそのアイコン展開を制作・改訂・レビューする時（その計画・作業項目の起草を含む） | `references/logo-quality.md` |
| ユーザーが法令上の義務やプライバシーの確認を依頼した時と、不特定のエンドユーザーへ公開・提供するWebサービスやアプリで、第三者サーバーからのリソース読み込みや解析・広告タグ、操作や入力の記録、宣伝メールの一斉送信、定期課金、エンドユーザーの投稿の公開または会員登録を新設・変更する時（その計画・作業項目の起草またはレビューを含む）。自分用ツール、社内システム、ユーザーが自分で開く単体HTML成果物は、確認の依頼と社外の不特定のエンドユーザーへ公開範囲を広げる変更の場合だけ該当する | `references/public-service-obligations.md` |
