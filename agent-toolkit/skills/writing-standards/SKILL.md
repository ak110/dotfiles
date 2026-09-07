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
| コード・テストコード | `references/writing.md`、`references/coding.md` |
| コーディングエージェント向け文書（`AGENTS.md`・`CLAUDE.md`・ルール・`SKILL.md`・サブエージェント定義・`references/`） | `references/writing.md`、`references/agent-documents.md` |

## 文章の作成時に読む資料

- 計画ファイルの起草で新しい概念名・識別子を導入する場面、命名場面、及び同名・同種の対象を複数の主体間で記述する場面: `references/referent-table.md`
- コード・スクリプトへコメントを書く前: `references/comment-granularity.md`
- 節又は文書を新規に起草する場面、表記検査時、lint違反の対処時、lint設定を緩和する時、及び口調の自己点検時は次の5件を併読する
  - `references/notation-rules.md`
  - `references/textlint-violations.md`
  - `references/lint-relax-criteria.md`
  - `references/tone-examples.md`
  - `references/tone-examples-llm-tone.md`

これら5件は互いに規範を委ねるため、いずれか1件だけを読む使い方をしない。

## コードの編集時に読む資料

コード編集に着手する前に、編集対象の拡張子に対応する言語別資料を読む。
言語固有の規約は`references/coding.md`に無く、読まずに書くと当該言語の必須事項を満たせない。
対応する資料が無い場合だけ共通品質のみで進める。
プロジェクトの依存定義（`package.json`等）または`<script>`読み込み等の技術利用痕跡から対象技術の利用が判明する場合は、対応するフレームワーク・ライブラリ別資料も読む。

言語別:

- Python: `references/python.md`と`references/python-references.md`
- TypeScript/TSX: `references/typescript.md`
- Rust: `references/rust.md`
- C#: `references/csharp.md`
- Bash/sh・bash・sh.tmpl: `references/bash.md`
- PowerShell/ps1・ps1.tmpl・psm1・psd1: `references/powershell.md`
- Windowsバッチ/cmd・bat: `references/windows-batch.md`
- Dockerfile: `references/dockerfiles.md`
- GitHub Actionsワークフロー（`.github/workflows/*.yaml`）: `references/github-actions.md`

フレームワーク・ライブラリ別:

- Tailwind CSS（v4系、依存名`tailwindcss`）: `references/tailwindcss.md`
- Alpine.js（v3系、依存名`alpinejs`または`<script>`読み込み）: `references/alpinejs.md`
- Playwright Test（v1系、依存名`@playwright/test`）: `references/playwright.md`
- Drizzle ORM/Drizzle Kit（0.x系、依存名`drizzle-orm`・`drizzle-kit`）: `references/drizzle.md`
- Svelte 5/SvelteKit 2（依存名`svelte`・`@sveltejs/kit`）: `references/svelte.md`

トピック別:

- 計画ファイルを作成する時点: `references/design-time.md`
- コードを編集する時点: `references/implementation-time.md`
- 計画ファイルの作成と実装を同じ主体が続けて実施する場合、及びコードレビューを実施する場合: 上記2資料の両方
- 設計判断を確定する時: `references/design-heuristics.md`と`references/implementation-time.md`
- 依存の追加・更新をする時: `references/dependency-management.md`と`references/implementation-time.md`
- テストコードを書く時: `references/testing.md`
- 文字エンコーディングを扱う時（日本語環境・ZIPファイル・Unicode正規化等）: `references/encoding.md`
- 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボード等）の作成・修正時: `references/independent-html.md`

## コーディングエージェント向け文書の編集時に読む資料

- 文書の新規作成、規範の主張・条件・適用範囲・例示を意味的に変更する改訂、文書間の分割・統合・配置先の設計、及びこれらの計画・レビュー: `references/llm-characteristics.md`
- スキル編集（公式リファレンスの参照先を含む）: `references/agent-skills.md`
- サブエージェント定義ファイルの編集、及びサブエージェントが関与する手順の作成・改訂: `references/sub-agents.md`
- 機械チェックスクリプトの新設・改修: `references/check-script-design.md`
- hook編集、及びhookのエンドユーザー向けメッセージの新設・改訂: `references/claude-hooks.md`と`references/hook-message-labeling.md`と`references/agent-skills.md`
- auto mode編集と権限拒否時: `references/claude-hooks.md`と`references/auto-mode.md`と`references/agent-skills.md`
- セッション状態フラグを扱う編集: `references/session-state-flags.md`
- セッション記録の集計・分析: `references/session-records.md`
- 大量の文書読込、大規模ブロック置換、plugin資源のroot失効: `references/tool-operations.md`

意味を変えない誤字・句読点・リンクの修正だけの編集では、`references/llm-characteristics.md`の読込を省いてよい。
