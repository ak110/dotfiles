# コーディング品質

本書は、コード・テストコードの品質基準を定める。

## 適用範囲

全プロジェクトへ共通するコーディング規範と、言語横断で照合する標準ツール・構文方針を記述する。
言語・フレームワーク・ライブラリ固有の詳細は、`references/<言語>.md`または
`references/<フレームワーク・ライブラリ名>.md`へ置く。
プロジェクト固有の規約は当該プロジェクトの`CLAUDE.md`・`.claude/rules/`へ置く。

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
規範の本体は、設計時と実装時の工程別資料へ置く。
