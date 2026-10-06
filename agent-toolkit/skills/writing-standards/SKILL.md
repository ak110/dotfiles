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

# 成果物の品質基準

本スキルはドキュメント、コードおよびエージェント向け文書を書く主体へ品質基準を提供する。hookの実装とセッション状態ファイルの設計も、コードを書くときの基準として扱う。エージェントが作業中に取る行動の規範は、実行主体別のルールと各作業のスキルが定める。
レビュー担当とレビューイーの判断基準は`agent-toolkit:review-standards`が定める。
計画ファイルの成果物契約は`agent-toolkit:plan-mode`が定め、本スキルの対象外とする。

以下の読込表の各行は、その行の時点または条件が成立したら、条件が対象とする操作の前に同じ行の資料を全文読み、そのすべてを適用することを示す。
条件は起動時だけでなく作業の途中でも成立するため、成立を判定してから読む。
条件付きの資料を読まずに操作へ進むと、その資料が定める品質基準を適用できない。

## 成果物種別ごとの必読資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 成果物へ書く事実主張を調査する時 | `references/investigation.md` |
| 人間が読む文章（Markdown・README・技術文書・API文書、業務・仕様文書、体験を述べる文章、コメント、AWI・UWIの本文）を書く時 | `references/writing.md` |
| コード・テストコードを書く時 | 「コードの編集時に読む資料」の各行 |
| エージェント向け文書（`AGENTS.md`・`CLAUDE.md`・ルール・`SKILL.md`・サブエージェント定義・`references/`）を書く時 | 「エージェント向け文書の編集時に読む資料」の各行 |

## 文章の作成時に読む資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 文章を書く時と表記をチェックする時（他の行より先に読む） | `references/notation-rules.md` |
| 恒久的な成果物の文面案を執筆する前と、textlintの指摘へ対応する時 | `references/textlint-violations.md` |
| lint設定の緩和・無効化・除外指定を追加する時 | `references/lint-relax-criteria.md` |
| `notation-rules.md`か`textlint-violations.md`が節名で指す口調例から書き換えの処置を選ぶ時 | `references/tone-examples.md`、`references/tone-examples-llm-tone.md`のうち指された資料 |
| 人間向け文書の役割と残す内容を選ぶ時 | `references/document-types.md` |
| 新しい概念名または識別子を導入する時 | `references/referent-table.md` |
| 既存の対象を名前で指す時と新しい名前を付ける時 | `references/defined-names.md` |

コードとテストコードの執筆には「コードの編集時に読む資料」を適用する。

## コードの編集時に読む資料

コード編集に着手する前と、コードとテストコードのレビューで判定に入る前に、次の3段を順に実施する。レビューではレビュー対象の差分へ同じ3段で資料を選ぶ。

1. `references/writing.md`を全文読む。
2. 編集対象で使う技術に対応する行を後掲の技術別の読込表から選び、資料を全文読む。
3. 後掲の工程別の読込表のうち、着手する工程と対象に該当する行の資料を全文読む。

技術別の読込表は次のとおりとする。

| 時点または条件（対象の拡張子・ファイル名・パス・依存名） | 全文読む資料 |
| --- | --- |
| `py` | `references/python.md` |
| `ts`・`tsx` | `references/typescript.md` |
| `rs` | `references/rust.md` |
| `cs` | `references/csharp.md` |
| `sh`・`bash`・`sh.tmpl` | `references/bash.md` |
| `ps1`・`ps1.tmpl`・`psm1`・`psd1` | `references/powershell.md` |
| `cmd`・`bat` | `references/windows-batch.md` |
| `Dockerfile` | `references/dockerfiles.md` |
| `.github/workflows/*.yaml` | `references/github-actions.md` |
| `tailwindcss`（v4系） | `references/tailwindcss.md` |
| `alpinejs`（v3系。`<script>`読み込みを含む） | `references/alpinejs.md` |
| `@playwright/test`・`playwright`（Playwright Test v1系とPythonバインディング） | `references/playwright.md` |
| `sqlalchemy`（2.0系） | `references/sqlalchemy.md` |
| `drizzle-orm`・`drizzle-kit`（0.x系） | `references/drizzle.md` |
| `svelte`・`@sveltejs/kit`（Svelte 5・SvelteKit 2） | `references/svelte.md` |

工程別の読込表は次のとおりとする。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 計画ファイルを作成する時点 | `references/design-time.md` |
| コードを編集する時点、設計判断を確定する時、および依存の追加・更新をする時 | `references/implementation-time.md` |
| 設計判断を確定する時、計画と実装を同じ主体が続けて実施する場合、およびコードレビューを実施する場合 | `references/design-heuristics.md` |
| 依存の追加・更新をする時 | `references/dependency-management.md` |
| MCPサーバーのツール、説明、応答を設計、実装、変更またはレビューする時 | `references/mcp-server-design.md` |
| テストコードを書く時とレビューする時、および条件分岐と判定条件を新設または変更する時 | `references/testing.md` |
| 文字エンコーディングを扱う時（日本語環境・ZIPファイル・Unicode正規化等） | `references/encoding.md` |
| 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボード等）の作成・修正時 | `references/independent-html.md` |
| エンドユーザーが操作する画面（HTML、CSS、画面コンポーネント、単体HTML成果物など）の新設・変更、その計画またはレビューをする時 | `references/ui-ux.md` |
| 前行の画面をHTML、CSS、JavaScriptで実装またはレビューする時 | `references/ui-ux-web-rules.md` |
| 前々行の画面がフォーム、一覧・データ表、検索、通知、モーダル・パネル、AI機能、同意・解約または多言語表示を含む時 | `references/ui-ux-patterns.md` |

## エージェント向け文書の編集時に読む資料

エージェント向け文書の編集に着手する前に、次の2段を順に実施する。

1. `references/writing.md`と`references/agent-documents-basics.md`を全文読む。
2. 後掲の対象別の読込表のうち、編集対象に該当する行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| スキル編集（公式リファレンスの参照先を含む） | `references/agent-skills.md` |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `references/sub-agents.md` |
| hook編集（auto modeのカスタムルール編集やセッション状態の編集に伴うものを含む）、およびhookのエンドユーザー向けメッセージの新設・改訂 | `references/agent-skills.md`、`references/claude-hooks.md` |
| auto modeのカスタムルール編集 | `references/auto-mode.md`、`references/agent-skills.md`。権限拒否に遭遇した場面の手順は`agent-toolkit:user-confirmation-and-report`が扱う |
| セッション状態ファイルまたはフラグを扱う編集（hook編集とauto modeのカスタムルール編集で扱う場合を含む） | `references/session-state-and-flags.md` |
| セッション記録の集計・分析 | `references/session-records.md` |
| 機械チェックスクリプトの新設・改修 | `references/check-script-design.md` |
| エージェント向け文書へ新しい規定を追記する場面、および文書の記述量を管理する場面 | `references/agent-documents-additions.md` |
