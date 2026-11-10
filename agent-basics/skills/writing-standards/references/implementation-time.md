# 実装時のコーディング品質

本書は、実際にコードを編集する際に適用する記述順序、データ処理、リソース管理、セキュリティ、および機械的チェックに関する規範をまとめたものである。
計画ファイルを作成する段階で確定しておくべき規範については、`design-time.md`を参照する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 指摘・エラーの原因分析と類似見直しを行う前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `design-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `design-time-implementation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time-implementation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `encoding.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/encoding.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `quality-principles-qcd.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles-qcd.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/review-only-criteria.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/review-only-criteria.md` |
| 「コードの簡潔さ」で処理の共通化を判断する時 | `design-heuristics-responsibility.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-responsibility.md` |
| 内部の引数や選択肢の整理でテストを統合する前 | `testing-priorities.md`、`testing-selection.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-priorities.md`、`read:agent-basics:*/skills/writing-standards/references/testing-selection.md` |
| 外部通信の時間予算や再試行を選ぶ前 | `design-runtime-jobs.md`、`design-runtime-services.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-jobs.md`、`read:agent-basics:*/skills/writing-standards/references/design-runtime-services.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 編集前の読解・記述順序と命名・コードの簡潔さ・言語横断のツールと構文に関する判断・作業の前 | `implementation-time-style.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-style.md` |
| コメントの記述粒度に関する判断・作業の前 | `implementation-time-comments.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-comments.md` |
| データと計算に関する判断・作業の前 | `implementation-time-data.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-data.md` |
| リソースと並行処理に関する判断・作業の前 | `implementation-time-resources.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-resources.md` |
| セキュリティ・ロギング・エラー処理に関する判断・作業の前 | `implementation-time-errors.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-errors.md` |
| 依存と自動生成物・lintと機械チェックに関する判断・作業の前 | `implementation-time-checks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-checks.md` |
