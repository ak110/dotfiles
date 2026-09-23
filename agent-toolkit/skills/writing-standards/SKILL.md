---
name: writing-standards
description: >
  ドキュメント・コメント・コード・テストコード・コーディングエージェント向け文書
  （`AGENTS.md`・`CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に最初に必ず呼び出す。
  AWI・UWIの本文起草時、成果物へ書く事実主張の裏付け調査時、ホスト機能の可否・入出力契約の調査時、
  セッション記録（`~/.claude/projects`配下）の集計・分析時も呼び出す。
  規範・基準・手順・目標値の妥当性、到達性、有無、置き場所を確認する場面でも呼び出す。
  formatter・linter・testerその他の品質検査を起動する時も呼び出す。
  理解のためコードを読むだけの場合はトリガー不要。
# 編集時の注意点:
# 著者向けの品質基準だけを扱い、レビュー担当とレビューイーの判断指針はreview-standardsを正本とする。
# 規範本文はreferences/配下へ置き、本体には目的と読込条件だけを置く。
---

# 成果物の品質基準

本スキルは、ドキュメント、コード及びコーディングエージェント向け文書を書く主体へ品質基準を提供する。
着手する作業に該当する参照資料を全文読み、そのすべてを適用する。
本スキルが「<条件>のとき: <参照先>」の形で挙げる参照先は、その条件が成立した時点で全文読む。条件は起動時だけでなく作業の途中でも成立するため、成立を判定してから読み、読む前にその条件が成立する操作へ着手しない。
条件付きの参照先を読まずに操作へ進むと、その参照先が定める手段の選定と出力量の制御が発動しない。
レビュー担当とレビューイーの判断基準は`agent-toolkit:review-standards`が定める。
計画ファイルの成果物契約は`agent-toolkit:plan-mode`が定め、本スキルの対象外とする。

## 成果物種別ごとの必読資料

成果物へ書く事実主張を調査する場合は`references/investigation.md`を全文読む。

| 成果物 | 全文読む資料 |
| --- | --- |
| 人間が読む文章（Markdown・README・技術文書・API文書、コメント、AWI・UWIの本文） | `references/writing.md` |
| コード・テストコード | `references/writing.md` |
| コーディングエージェント向け文書（`AGENTS.md`・`CLAUDE.md`・ルール・`SKILL.md`・サブエージェント定義・`references/`） | `references/writing.md`、`references/agent-documents-basics.md` |

## 文章の作成時に読む資料

文章を書く時と表記を検査する時は、まず`references/notation-rules.md`を全文読む。
同資料は表記規則の目次と検査手段を持つ。該当する節が、textlint違反、lint緩和の判定、口調の対比集の各資料への条件付きの参照を示す。

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
- 管理対象一時領域を扱う時: `references/managed-temp.md`
- リポジトリ内を検索する時: `references/search.md`
- 秘匿値ファイルを扱う時: `references/security.md`

## コーディングエージェント向け文書の編集時に読む資料

コーディングエージェント向け文書の編集に着手する前に、次の3段を順に実施する。

1. `references/llm-characteristics.md`を全文読む。同資料は後続2段の設計入力となる読者特性を扱う。
2. `references/writing.md`と`references/agent-documents-basics.md`を全文読む。
3. 後掲の対象別資料のうち、編集対象に該当するものを全文読む。

手順3の対象別資料は次のとおりとする。

- スキル編集（公式リファレンスの参照先を含む）: `references/agent-skills.md`
- サブエージェント定義ファイルの編集、及びサブエージェントが関与する手順の作成・改訂: `references/sub-agents.md`
- hook編集、及びhookのエンドユーザー向けメッセージの新設・改訂: `references/agent-skills.md`を読み、`agent-toolkit:hook-implementation`を起動する
- auto modeのカスタムルール編集: `references/auto-mode.md`と`references/agent-skills.md`を読み、`agent-toolkit:hook-implementation`を起動する。権限拒否に遭遇した場面の手順は`agent-toolkit:confirmation-and-uwi`が扱う
- セッション状態フラグを扱う編集: `agent-toolkit:hook-implementation`を起動する
- セッション記録の集計・分析: `references/session-records.md`
- 機械チェックスクリプトの新設・改修: `references/check-script-design.md`
- 規範文書へ新しい規定を追記する場面、及び文書の記述量を管理する場面: `references/agent-documents-additions.md`

## 品質検査の実行時に読む資料

- formatter、linter、tester又はプロジェクト固有の検査を起動する時点: `references/check-execution.md`
