# 検証結果と手動観測の保存

実装を担当するメインエージェントおよびレーン担当者が変更範囲を検証し、手動で観測した結果を返却時の`検証結果`や進捗ログへ記録するための共通手順である。
手動観測する前には`verification-evidence-observation.md`「手動観測の保存」を、`検証結果`を記述する前には`verification-evidence-records.md`「完成条件証拠の検証記録」をそれぞれ参照する。
工程の認可や、実装とレビューの役割分担については、呼出側との契約を適用する。
最終観測の取得順序は`agent-toolkit:commit`の`references/commit-verification.md`「検証」に従う。
共通する検証のまとめ実行と保存結果の被覆判定は`agent-basics:check-execution/references/verification-scope-reruns.md`「実行条件と再実行」に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| OSを模擬した試験の保証と環境条件を記録する前 | `agent-basics:writing-standards/references/testing-selection.md`、`agent-basics:writing-standards/references/testing-platforms.md` |
| 受入シナリオを起動する前 | `agent-toolkit:delegation/references/acceptance-scenarios.md` |
| 訂正済み検証表を使う前 | `../skills/write-plan/references/plan-file-standards-verification.md` |
| 保存出力を取得する前 | `check-execution-operations-output.md` |
| 取得版の異なる観測を検証結果へ書く前 | `exec-review-recording-grounds.md` |
| レビューへ渡す最終観測を取得する前 | `../skills/commit/references/commit-verification.md` |
| 保存済みの成功集合と限定再検証から被覆を判定する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 手動観測の保存・検証時本文の保存に関する判断・作業の前 | `verification-evidence-observation.md` |
| 完成条件証拠の検証記録に関する判断・作業の前 | `verification-evidence-records.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| コミットの作成・履歴統合に着手する前 | `../skills/commit/SKILL.md` |
