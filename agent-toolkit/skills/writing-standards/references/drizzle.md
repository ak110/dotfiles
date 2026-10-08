# Drizzle ORM／Drizzle Kit記述スタイル

本書はDrizzle ORMとDrizzle Kitを用いる実装の記述スタイル基準を定める。
対象バージョン: drizzle-orm/drizzle-kit 0.x系（参考実利用バージョン: drizzle-orm 0.45・drizzle-kit 0.31）。公式ドキュメントは<https://orm.drizzle.team/docs/overview>を参照する。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/drizzle.md：H1直下：2026年9月16日」にある。

## スキーマ定義

- スキーマを型の基準にし、推論で二重管理を避ける。機能境界で分けても設定から全体へ到達できるようにする
- Relational Query APIが使うリレーションは明示的に定義する

## マイグレーション運用

- 本番運用は`drizzle-kit generate`での生成と`drizzle-kit migrate`での適用の2段階を通常の手順とし、生成物（`drizzle/`配下の`*.sql`・`snapshot.json`）はリポジトリにコミットする（環境ごとの再現性を担保し、スキーマ変更履歴を監査可能にするため）
- `drizzle-kit push`は差分を即座にDBへ反映するがマイグレーション履歴を残さない。履歴不在はロールバック手段の喪失とチーム間の状態不整合に直結するため、ローカルプロトタイピングに限定する
- 本番適用専用のconfig（例: `drizzle-prod.config.ts`）を分離し、`migrate`実行時に`--config`で明示切り替える

## クエリの書き方

- 条件の構成や集計はquery builder、関連を含む取得はRelational Query APIを選び、N+1を避ける
- 頻出クエリはprepared statementによる実行計画の再利用を検討する

## トランザクション・接続管理

- 複数テーブルにまたがる更新は`db.transaction(async (tx) => { ... })`でまとめ、`tx`経由で全クエリを実行する（`db`を直接使うと個別コミットになり原子性が保証されないため）
- 行ロックが必要な更新は、対応する方言（PostgreSQL・MySQL等）では`.for('update')`・`.skipLocked()`等をトランザクション内で使う。SQLite等の行ロック構文を持たない方言では適用外とする
- 接続プールはドライバーで管理し、Drizzleインスタンスの寿命をアプリケーションへそろえる

## SQLインジェクション対策

- 動的な値を含むSQLは`sql`タグ付きテンプレート（`` sql`select * from ${table} where ${table.id} = ${id}` ``）で組み立て、文字列結合による生SQL構築はしない
- ユーザー入力に由来するテーブル名・カラム名の動的指定は避け、定義済みのテーブル・カラムオブジェクトの参照に限定する（識別子はプレースホルダー化できず、許可リスト方式でしか安全に扱えないため）

## drizzle.config.tsの推奨設定

- schema・out・dialect・dbCredentialsを明示し、環境と生成先を確定する
- `dbCredentials.url`は環境変数経由で注入し、設定ファイルへ直書きしない
