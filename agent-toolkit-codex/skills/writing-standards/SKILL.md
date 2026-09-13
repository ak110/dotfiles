---
name: writing-standards
description: >
  ドキュメント・コメント・コード・テストコード・コーディングエージェント向け文書
  （`AGENTS.md`・`CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に最初に必ず呼び出す。
  AWI・UWIの本文起草時、ホスト機能の可否・入出力契約の調査時、
  セッション記録（`~/.claude/projects`配下）の集計・分析時も呼び出す。
  規範・基準・手順・目標値の妥当性、到達性、有無、置き場所を確認する場面でも呼び出す。
  理解のためコードを読むだけの場合はトリガー不要。
# 編集時の注意点:
# 著者向けの品質基準だけを扱い、レビュー担当とレビューイーの判断指針はreview-standardsを正本とする。
# 規範本文はreferences/配下へ置き、本体には目的と読込条件だけを置く。
---

# 成果物の品質基準

本スキルは、ドキュメント、コード及びコーディングエージェント向け文書を書く主体へ品質基準を提供する。
着手する作業に該当する参照資料を全文読み、そのすべてを適用する。
レビュー担当とレビューイーの判断基準は`agent-toolkit:review-standards`が定める。
計画ファイルの成果物契約は`agent-toolkit:plan-mode`が定め、本スキルの対象外とする。

## 成果物種別ごとの必読資料

| 成果物 | 全文読む資料 |
| --- | --- |
| 人間が読む文章（Markdown・README・技術文書・API文書、コメント、AWI・UWIの本文） | `references/writing.md` |
| コード・テストコード | `references/writing.md` |
| コーディングエージェント向け文書（`AGENTS.md`・`CLAUDE.md`・ルール・`SKILL.md`・サブエージェント定義・`references/`） | `references/writing.md`、`references/agent-documents-basics.md` |

## 文章の作成時に読む資料

節又は文書を新規に起草する場面、表記検査時、lint違反の対処時、lint設定を緩和する時、及び口調の自己点検時は、
まず`references/notation-rules.md`を全文読む。
同資料は表記規則の目次と検査手段を持ち、該当する節が次の4資料への条件付きの参照を示す。

- `references/textlint-violations.md`
- `references/lint-relax-criteria.md`
- `references/tone-examples.md`
- `references/tone-examples-llm-tone.md`

次の場面では併せて対応する資料を全文読む。

- 計画ファイルの起草で新しい概念名・識別子を導入する場面、命名場面、及び同名・同種の対象を複数の主体間で記述する場面: `references/referent-table.md`

## コードの編集時に読む資料

コード編集に着手する前に、次の3段を順に実施する。

1. `references/writing.md`を全文読む。
2. 後掲の対応表の左列を、編集対象の拡張子、プロジェクトの依存定義（`package.json`の`dependencies`など）又は`<script>`読み込みの技術利用痕跡へ照合し、該当する行の資料を全文読む。該当する行が1つも無い場合だけ共通品質のみで進める。
3. 後掲の条件付き資料のうち、当該工程と対象に該当するものを全文読む。

手順2の対応表は次のとおりとする。

| 対象の拡張子・依存名 | 全文読む資料 |
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
| `drizzle-orm`・`drizzle-kit`（0.x系） | `references/drizzle.md` |
| `svelte`・`@sveltejs/kit`（Svelte 5・SvelteKit 2） | `references/svelte.md` |

手順3の条件付き資料は次のとおりとする。

- 計画ファイルを作成する時点: `references/design-time.md`
- コードを編集する時点: `references/implementation-time.md`
- 計画ファイルの作成と実装を同じ主体が続けて実施する場合、及びコードレビューを実施する場合: 上記2資料の両方
- 設計判断を確定する時: `references/design-heuristics.md`と`references/implementation-time.md`
- 依存の追加・更新をする時: `references/dependency-management.md`と`references/implementation-time.md`
- テストコードを書く時: `references/testing.md`
- 文字エンコーディングを扱う時（日本語環境・ZIPファイル・Unicode正規化等）: `references/encoding.md`
- 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボード等）の作成・修正時: `references/independent-html.md`

## コーディングエージェント向け文書の編集時に読む資料

コーディングエージェント向け文書の編集に着手する前に、次の3段を順に実施する。

1. `references/llm-characteristics.md`を全文読む。同資料は後続2段の設計入力となる読者特性を扱う。
2. `references/writing.md`と`references/agent-documents-basics.md`を全文読む。
3. 後掲の対象別資料のうち、当該編集対象に該当するものを全文読む。

手順1は、意味を変えない誤字・句読点・リンクの修正だけの編集では省いてよい。

手順3の対象別資料は次のとおりとする。

- スキル編集（公式リファレンスの参照先を含む）: `references/agent-skills.md`
- サブエージェント定義ファイルの編集、及びサブエージェントが関与する手順の作成・改訂: `references/sub-agents.md`
- hook編集、及びhookのエンドユーザー向けメッセージの新設・改訂: `references/claude-hooks.md`と`references/agent-skills.md`
- auto mode編集と権限拒否時: `references/claude-hooks.md`と`references/auto-mode.md`と`references/agent-skills.md`
- セッション状態フラグを扱う編集: `references/session-state-and-flags.md`
- セッション記録の集計・分析: `references/session-records.md`
- 機械チェックスクリプトの新設・改修: `references/check-script-design.md`
- 規範文書へ新しい規定を追記する場面、及び文書の記述量を管理する場面: `references/agent-documents-additions.md`

## 品質検査の実行時に読む資料

- formatter、linter、tester又はプロジェクト固有の検査を起動する時点: `references/check-execution.md`
