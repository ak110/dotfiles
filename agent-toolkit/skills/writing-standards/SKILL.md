---
name: writing-standards
description: >
  ドキュメント・コメント・コード・テストコード・コーディングエージェント向け文書
  （`AGENTS.md`・`CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に最初に必ず呼び出す。
  AWI・UWIの本文起草時、成果物へ書く事実主張の裏付け調査時、ホスト機能の可否・入出力契約の調査時、
  セッション記録（`~/.claude/projects`配下）の集計・分析時も呼び出す。
  規範・基準・手順・目標値の妥当性、到達性、有無、置き場所を確認する場面でも呼び出す。
  理解のためコードを読むだけの場合はトリガー不要。
# 編集時の注意点:
# 著者向けの品質基準だけを扱い、レビュー担当とレビューイーの判断指針はreview-standardsを正本とする。
# 規範本文はreferences/配下へ置き、本体には目的と読込条件だけを置く。
---

# 成果物の品質基準

本スキルはドキュメント、コード及びコーディングエージェント向け文書を書く主体へ品質基準を提供する。hookの実装とセッション状態ファイルの設計も、コードを書くときの基準として扱う。エージェントが作業中に取る行動の規範は、実行主体別のルールと各作業のスキルが定める。
着手する作業に該当する参照資料を全文読み、そのすべてを適用する。
本スキルが「<条件>のとき: <参照先>」の形で挙げる参照先は、その条件が成立した時点で全文読む。条件は起動時だけでなく作業の途中でも成立するため、成立を判定してから読み、読む前にその条件が成立する操作へ着手しない。
条件付きの参照先を読まずに操作へ進むと、その参照先が定める品質基準を適用できない。
レビュー担当とレビューイーの判断基準は`agent-toolkit:review-standards`が定める。
計画ファイルの成果物契約は`agent-toolkit:plan-mode`が定め、本スキルの対象外とする。

## 成果物種別ごとの必読資料

成果物へ書く事実主張を調査する場合は`references/investigation.md`を全文読む。

| 成果物 | 全文読む資料 |
| --- | --- |
| 人間が読む文章（Markdown・README・技術文書・API文書、コメント、AWI・UWIの本文） | `references/writing.md` |
| コード・テストコード | `references/writing.md` |
| コーディングエージェント向け文書（`AGENTS.md`・`CLAUDE.md`・ルール・`SKILL.md`・サブエージェント定義・`references/`） | 後掲「コーディングエージェント向け文書の編集時に読む資料」に従う |

## 文章の作成時に読む資料

文章を書く時と表記をチェックする時は、まず`references/notation-rules.md`を全文読む。
同資料は表記規則の目次とチェック手段を持つ。該当する節が、textlint違反、lint緩和の判定、口調の対比集の各資料への条件付きの参照を示す。

新しい概念名又は識別子を導入する時は、併せて`references/referent-table.md`を全文読む。

## コードの編集時に読む資料

コード編集に着手する前に、次の3段を順に実施する。

1. `references/writing.md`を全文読む。
2. 編集対象で使う技術に対応する資料を後掲の対応表から選び、全文読む。
3. 後掲の条件付き資料のうち、着手する工程と対象に該当するものを全文読む。

手順2の対応表は次のとおりとする。

| 対象の拡張子・ファイル名・パス・依存名 | 全文読む資料 |
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

手順3の条件付き資料は次のとおりとする。

- 計画ファイルを作成する時点: `references/design-time.md`
- コードを編集する時点、設計判断を確定する時、及び依存の追加・更新をする時: `references/implementation-time.md`
- 設計判断を確定する時、計画と実装を同じ主体が続けて実施する場合、及びコードレビューを実施する場合: `references/design-heuristics.md`
- 依存の追加・更新をする時: `references/dependency-management.md`
- テストコードを書く時、及び条件分岐と判定条件を新設又は変更する時: `references/testing.md`
- 文字エンコーディングを扱う時（日本語環境・ZIPファイル・Unicode正規化等）: `references/encoding.md`
- 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボード等）の作成・修正時: `references/independent-html.md`
- エンドユーザーが操作する画面（HTML、CSS、画面コンポーネント、単体HTML成果物など）の新設・変更、その計画又はレビューをする時: `references/ui-ux.md`
- 前項の画面をHTML、CSS、JavaScriptで実装又はレビューする時: `references/ui-ux-web-rules.md`
- 前々項の画面がフォーム、一覧・データ表、検索、通知、モーダル・パネル、AI機能、同意・解約又は多言語表示を含む時: `references/ui-ux-patterns.md`

## コーディングエージェント向け文書の編集時に読む資料

コーディングエージェント向け文書の編集に着手する前に、次の3段を順に実施する。

1. `references/llm-characteristics.md`を全文読む。同資料は後続2段の設計入力となる読者特性を扱う。
2. `references/writing.md`と`references/agent-documents-basics.md`を全文読む。
3. 後掲の対象別資料のうち、編集対象に該当するものを全文読む。

手順3の対象別資料は次のとおりとする。

- スキル編集（公式リファレンスの参照先を含む）: `references/agent-skills.md`
- サブエージェント定義ファイルの編集、及びサブエージェントが関与する手順の作成・改訂: `references/sub-agents.md`
- hook編集、及びhookのエンドユーザー向けメッセージの新設・改訂: `references/agent-skills.md`と`references/claude-hooks.md`。セッション状態ファイル又はフラグを扱う場合は`references/session-state-and-flags.md`も読む
- auto modeのカスタムルール編集: `references/auto-mode.md`と`references/agent-skills.md`。hookを編集する場合は`references/claude-hooks.md`も読む。権限拒否に遭遇した場面の手順は`agent-toolkit:confirmation-and-uwi`が扱う
- セッション状態ファイル又はフラグを扱う編集: `references/session-state-and-flags.md`。hookの実装も編集する場合は`references/claude-hooks.md`も読む
- セッション記録の集計・分析: `references/session-records.md`
- 機械チェックスクリプトの新設・改修: `references/check-script-design.md`
- 規範文書へ新しい規定を追記する場面、及び文書の記述量を管理する場面: `references/agent-documents-additions.md`
