# Drizzle ORM／Drizzle Kit記述スタイル

対象バージョン: drizzle-orm/drizzle-kit 0.x系（参考実利用バージョン: drizzle-orm 0.45・drizzle-kit 0.31）。公式ドキュメントは<https://orm.drizzle.team/docs/overview>を参照する。

## スキーマ定義

- スキーマは`sqliteTable`/`pgTable`等のテーブル定義関数で宣言し、型はコードから推論させる（手書きの型定義との二重管理による乖離を防ぐため）
- 行の型は`typeof table.$inferSelect`・`typeof table.$inferInsert`で取得する（別途importが不要なため）
- テーブル数が増える場合はスキーマを複数ファイルに分割し、`drizzle.config.ts`の`schema`にディレクトリを指定する（Drizzle Kitがディレクトリを再帰走査するため、機能単位で分けてもmigration生成は破綻しない）
- リレーションは`relations()`ヘルパーで明示的に宣言する（Relational Query APIがリレーション定義を前提とするため）

## マイグレーション運用

- 本番運用は`drizzle-kit generate`での生成と`drizzle-kit migrate`での適用の2段階を既定とし、生成物（`drizzle/`配下の`*.sql`・`snapshot.json`）はリポジトリにコミットする（環境ごとの再現性を担保し、スキーマ変更履歴を監査可能にするため）
- `drizzle-kit push`は差分を即座にDBへ反映するがマイグレーション履歴を残さない。履歴不在はロールバック手段の喪失とチーム間の状態不整合に直結するため、ローカルプロトタイピングに限定する
- 本番用マイグレーション実行は`--config=<production config>`のように環境別configを明示指定する

## クエリの書き方

- 単純なCRUD・複雑な集計・動的な条件組み立てはクエリビルダー（`db.select()`/`db.insert()`等）を使う
- 関連テーブルを含むネスト取得（`with`句）はRelational Query API（`db.query.*`）を使う（ネストしたJOINを1回のSQL文にまとめて発行し、アプリ側でのN+1クエリを回避できるため）
- 動的な`limit`/`offset`/`where`を伴う頻出クエリは`.prepare()`と`sql.placeholder()`でプリペアドステートメント化する（クエリプランの再利用によりレイテンシを削減できるため）

## トランザクション・接続管理

- 複数テーブルにまたがる更新は`db.transaction(async (tx) => { ... })`でまとめ、`tx`経由で全クエリを実行する（`db`を直接使うと個別コミットになり原子性が保証されないため）
- 行ロックが必要な更新は、対応する方言（PostgreSQL・MySQL等）では`.for('update')`・`.skipLocked()`等をトランザクション内で使う。SQLite等の行ロック構文を持たない方言では適用外とする
- コネクションプールはドライバー側（`pg`の`Pool`等）で管理し、Drizzleインスタンスはアプリ起動時に1回だけ生成する（リクエストごとの再接続はレイテンシとコネクション枯渇の原因になるため）

## SQLインジェクション対策

- 動的な値を含むSQLは`sql`タグ付きテンプレート（`` sql`select * from ${table} where ${table.id} = ${id}` ``）で組み立て、文字列結合による生SQL構築はしない
- ユーザー入力に由来するテーブル名・カラム名の動的指定は避け、定義済みのテーブル・カラムオブジェクトの参照に限定する（識別子はプレースホルダー化できず、許可リスト方式でしか安全に扱えないため）

## drizzle.config.tsの推奨設定

- `schema`・`out`・`dialect`・`dbCredentials`を明示指定する（省略時のデフォルト解決に依存すると環境差異でマイグレーション生成先がずれるため）
- `dbCredentials.url`は環境変数経由で注入し、設定ファイルへ直書きしない
- 本番適用専用のconfig（例: `drizzle-prod.config.ts`）を分離し、`migrate`実行時に`--config`で明示切り替える
