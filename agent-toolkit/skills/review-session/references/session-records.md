# セッション記録の構造と集計

本書は、Claude CodeとCodexのセッション記録を集計・分析する際に必要なデータ構造の知識をまとめたものである。
ここでいうセッション記録とは、Claude CodeおよびCodexが保存する会話履歴（transcript）を指す。

Claude Codeの記録は`~/.claude/projects`配下に保存される。
Codexのロールアウト記録は`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`に保存される（`CODEX_HOME`未設定時は`~/.codex`）。
ファイル名に含まれる`<thread-id>`はUUIDであり、末尾の5区画がthread IDに該当する。
どちらの記録も形式はJSON Linesであり、各レコードの時刻フィールド名は共通して`timestamp`である。

コンパクション（会話圧縮）の記録形式は、実行環境（runtime）によって異なる。
Claude Codeでは`type`が`system`、`subtype`が`compact_boundary`のレコードとして記録され、`compactMetadata`の中に`trigger`、`preTokens`、`postTokens`、`durationMs`が格納される。
一方、Codexでは`type`が`compacted`のレコードに、所要時間を除いた情報が記録される。
監査記録は`docs/development/audit-records-session-records.md`の「agent-toolkit/skills/review-session/references/session-records.md：H1直下：2026年9月2日」に記載されている。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Claude Codeの記録・スキル起動の判定・暗号化された値・進捗更新の判別に関する判断・作業の前 | `session-records-formats.md` |
| 集計値の典拠・複数セッションの比較結果に関する判断・作業の前 | `session-records-metrics.md` |
| 本文の検索に関する判断・作業の前 | `session-records-query.md` |
| 会話の少量取得に関する判断・作業の前 | `session-records-conversation.md` |
