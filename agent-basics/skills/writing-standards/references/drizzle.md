# Drizzle ORM／Drizzle Kit記述スタイル

本書は、Drizzle ORMおよびDrizzle Kitを使用する際の実装コードの記述スタイル基準をまとめたものである。
対象バージョンは、drizzle-orm/drizzle-kitの0.x系（参考となる実利用バージョン: drizzle-orm 0.45、drizzle-kit 0.31）である。公式ドキュメントは <https://orm.drizzle.team/docs/overview> を参照する。
監査記録は、`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/drizzle.md：Drizzle ORM／Drizzle Kit記述スタイル：2026年9月16日」にある。

## スキーマ定義

- スキーマ定義を型の基準とし、TypeScriptの型はスキーマから推論させて二重管理を防ぐ。Relational Query APIで利用するリレーションは明示的に定義する。

## マイグレーション運用

- 本番環境向けのマイグレーションは、`drizzle-kit generate`によるSQL生成と、`drizzle-kit migrate`による適用の2段階に分ける。生成された成果物（`drizzle/`配下の`*.sql`および`snapshot.json`）はGitにコミットする。
- `drizzle-kit push`はマイグレーション履歴が残らないため、ローカルでのプロトタイプ開発でのみ使用する。
- 本番環境への適用専用の設定ファイル（例: `drizzle-prod.config.ts`）を個別に用意し、`migrate`の実行時に`--config`オプションで切り替える。

## クエリとトランザクション

- 条件の組み立てや集計処理にはQuery Builderを使い、リレーションを含むデータ取得にはRelational Query APIを選んでN+1問題を防ぐ。
- 複数テーブルにまたがる更新処理は`db.transaction(async (tx) => { ... })`でひとまとめにし、すべてのクエリを`tx`経由で実行する（`db`を直接呼び出すと個別にコミットされてしまう）。
- 行ロックが必要な場合は、対応しているDB方言（PostgreSQLやMySQLなど）において、トランザクション内で`.for('update')`や`.skipLocked()`などを使用する。SQLiteなどの行ロック構文を持たない方言では対象外とする。

## SQLインジェクション対策

- 動的な値を含む生のSQL文は`sql`タグ付きテンプレートリテラルで安全に組み立て、文字列結合は行わない。
- ユーザー入力に由来するテーブル名やカラム名を動的に指定することは避け、定義済みのテーブルオブジェクトやカラムオブジェクトを参照する（識別子はSQLのプレースホルダーとしてバインドできないため）。

## drizzle.config.ts

- 設定ファイルではschema、out、dialect、dbCredentialsを明示的に指定し、`dbCredentials.url`の値は環境変数から注入する。
