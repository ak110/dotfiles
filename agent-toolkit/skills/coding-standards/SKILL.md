---
name: coding-standards
description: >
  コード・テストコードを新規作成または修正する時、その計画時、コードレビュー時に最初に必ず呼び出すこと。
  コメントの日本語表現は`agent-toolkit:writing-standards`と併用する。
  単に理解のためコードを読むだけの場合や、ドキュメントのみの編集ではトリガー不要。
---

# コーディング品質

本スキルは、コード・テストコードの品質基準を提供する。

## 適用範囲

全プロジェクトへ共通するコーディング規範と、言語横断で照合する標準ツール・構文方針を記述する。
言語・フレームワーク・ライブラリ固有の詳細は、`references/<言語>.md`または
`references/<フレームワーク・ライブラリ名>.md`へ置く。
プロジェクト固有の規約は当該プロジェクトの`CLAUDE.md`・`.claude/rules/`へ置く。

コード編集に着手する前に、編集対象の拡張子に対応する`references/<言語>.md`を必ずReadで読み込む
（変更規模を問わず省略しない。対応ファイルが無い場合に限り共通品質のみで進める）。
プロジェクトの依存定義（`package.json`等）または`<script>`読み込み等の技術利用痕跡から対象技術の利用が判明する場合は、対応する`references/<フレームワーク・ライブラリ名>.md`も読み込む。

言語別リファレンス:

- Python: `references/python.md`と`references/python-references.md`
- TypeScript/TSX: `references/typescript.md`
- Rustのコードを編集する時は`agent-toolkit/skills/coding-standards/references/rust.md`を全文読む
- C#: `references/csharp.md`
- Bash/sh・bash・sh.tmpl: `references/bash.md`
- PowerShell/ps1・ps1.tmpl・psm1・psd1: `references/powershell.md`
- Windowsバッチ/cmd・bat: `references/windows-batch.md`
- Dockerfile: `references/dockerfiles.md`
- GitHub Actionsワークフロー（`.github/workflows/*.yaml`）: `references/github-actions.md`

トピック別リファレンス:

- 設計判断を確定する時は`agent-toolkit/skills/coding-standards/references/design-heuristics.md`を全文読み、
  該当する定石とトレードオフを確認する
- 依存の追加・更新をする時は`agent-toolkit/skills/coding-standards/references/dependency-management.md`を全文読む
  （バージョン固定方針・脆弱性確認・ロックファイル運用・パッケージマネージャー個別の注意点）
- 文字エンコーディング詳細（日本語環境・ZIPファイル・Unicode正規化等）: `references/encoding.md`
- 単体HTML成果物（ユーザーへ単体で提示するレポート・ダッシュボード等。作成・修正時に必読）:
  `references/independent-html.md`
- テストコードを作成・修正する時は`agent-toolkit/skills/coding-standards/references/testing.md`を全文読む

フレームワーク・ライブラリ別リファレンス:

- Tailwind CSS（v4系、依存名`tailwindcss`）: `references/tailwindcss.md`
- Alpine.js（v3系、依存名`alpinejs`または`<script>`読み込み）: `references/alpinejs.md`
- Playwright Test（v1系、依存名`@playwright/test`）: `references/playwright.md`
- Drizzle ORM/Drizzle Kit（0.x系、依存名`drizzle-orm`・`drizzle-kit`）: `references/drizzle.md`
- Svelte 5/SvelteKit 2（依存名`svelte`・`@sveltejs/kit`）: `references/svelte.md`

## 言語横断のツールと構文

プロジェクトに指定が無い場合は、次の標準ツールを使う。

| 言語 | 依存管理・ビルド | lint・format・補助ツール |
| --- | --- | --- |
| Python | `uv` | `pyfltr`、`pytilpack` |
| TypeScript | `pnpm`、一度限りの実行は`pnpx` | `Biome`。未対応ルールが必要な場合だけESLintとPrettierを併用 |
| Rust | `cargo` | `cargo clippy -D warnings`、`cargo fmt` |
| C# | `dotnet` CLI | `dotnet format`、Roslynアナライザー、`Microsoft.CodeAnalysis.NetAnalyzers` |
| Bash | 該当なし | `shellcheck`、`shfmt` |
| PowerShell | 該当なし | PSScriptAnalyzer |

公開互換性として宣言した範囲で利用できる新しい言語機能を積極的に使う。
利用可否は対象プロジェクトの言語バージョンと公式リリースノートで確認する。
標準ライブラリで代替できる外部依存は排除する。

シェル系言語では、外部入力をコマンドの引数として渡す。
動的な実行が必要な場合は固定候補へ分岐し、評価系コマンドへ外部入力を渡さない。
外部入力を含むコマンド文字列を組み立てて実行しない。

## コーディング品質（全言語共通）

以下の規範は、`agent-toolkit/rules/01-agent.md`「判断指針」の具体例である（網羅規定ではなく、列挙外の場面でも原則に従う）。
規範の本体は工程別リファレンスへ置く。

- 計画ファイルを作成する時点で`references/design-time.md`を全文読む
- コードを編集する時点で`references/implementation-time.md`を全文読む
- 計画ファイルの作成と実装を同じ主体が続けて実施する場合と、コードレビューを実施する場合は、両方を全文読む
