# Drizzle ORM／Drizzle Kit記述スタイル

本書はDrizzle ORMとDrizzle Kitを用いる実装の記述スタイル基準を定める。
対象バージョン: drizzle-orm/drizzle-kit 0.x系（参考実利用バージョン: drizzle-orm 0.45・drizzle-kit 0.31）。公式ドキュメントは<https://orm.drizzle.team/docs/overview>を参照する。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/drizzle.md：H1直下：2026年9月16日」にある。

## スキーマ定義

- スキーマを型の基準にし、推論で二重管理を避ける。Relational Query APIが使うリレーションは明示的に定義する

## マイグレーション運用

- 本番は`drizzle-kit generate`での生成と`drizzle-kit migrate`での適用の2段階とし、生成物（`drizzle/`配下の`*.sql`・`snapshot.json`）をコミットする
- `drizzle-kit push`はマイグレーション履歴を残さないため、ローカルプロトタイピングに限る
- 本番適用専用のconfig（例: `drizzle-prod.config.ts`）を分け、`migrate`実行時に`--config`で切り替える

## クエリとトランザクション

- 条件の構成や集計はquery builder、関連を含む取得はRelational Query APIを選び、N+1を避ける
- 複数テーブルにまたがる更新は`db.transaction(async (tx) => { ... })`でまとめ、全クエリを`tx`経由で実行する（`db`を直接使うと個別にコミットされる）
- 行ロックは対応する方言（PostgreSQL・MySQLなど）でトランザクション内の`.for('update')`・`.skipLocked()`などを使う。SQLiteなど行ロック構文の無い方言では適用外とする

## SQLインジェクション対策

- 動的な値を含むSQLは`sql`タグ付きテンプレートで組み立て、文字列結合しない
- ユーザー入力に由来するテーブル名・カラム名の動的指定は避け、定義済みのテーブル・カラムオブジェクトの参照に限る（識別子はプレースホルダー化できないため）

## drizzle.config.ts

- schema・out・dialect・dbCredentialsを明示し、`dbCredentials.url`は環境変数から注入する
