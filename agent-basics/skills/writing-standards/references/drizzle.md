# Drizzle ORM／Drizzle Kit記述スタイル

対象の版: drizzle-orm・drizzle-kitの0.x系（参考とした実利用の版はdrizzle-orm 0.45、drizzle-kit 0.31。公式ドキュメント <https://orm.drizzle.team/docs/overview>）。
本書は、Drizzle ORMとDrizzle Kitを使うコードとマイグレーションを書く・直す時に読む。
マイグレーションの履歴とSQLの組み立ての規定は`agent-basics:writing-standards/references/quality-principles-constraints.md`の適用である。

## スキーマ定義

- TypeScriptの型はスキーマから推論させて二重に管理せず、Relational Query APIで使うリレーションは明示して定義する。

## マイグレーション運用

- 本番のマイグレーションは、`drizzle-kit generate`によるSQLの生成と`drizzle-kit migrate`による適用の2段階に分け、生成物（`drizzle/`の`*.sql`と`snapshot.json`）をGitへcommitする。
- `drizzle-kit push`は履歴が残らないため、ローカルの試作だけで使う。
- 本番へ適用する専用の設定ファイル（例: `drizzle-prod.config.ts`）を用意し、`migrate`の`--config`で切り替える。

## クエリとトランザクション

- 条件の組み立てと集計はQuery Builder、リレーションを含む取得はRelational Query APIで行い、N+1を避ける。
- 複数のテーブルにまたがる更新は`db.transaction(async (tx) => { ... })`でまとめ、全てのクエリを`tx`で実行する（`db`を直接呼ぶと個別にcommitされる）。
- 行ロックは、対応する方言（PostgreSQL、MySQLなど）のトランザクションの中で`.for('update')`や`.skipLocked()`を使う（SQLiteなど行ロックの構文が無い方言は対象外）。

## SQLインジェクション対策

- 動的な値を含む生のSQLは`sql`タグ付きテンプレートで組み立て、文字列を結合しない。
- 入力に由来するテーブル名とカラム名を動的に指定せず、定義済みのテーブルとカラムのオブジェクトを参照する（識別子はプレースホルダーでバインドできないため）。

## drizzle.config.ts

- `schema`、`out`、`dialect`、`dbCredentials`を明示し、`dbCredentials.url`は環境変数から渡す。
