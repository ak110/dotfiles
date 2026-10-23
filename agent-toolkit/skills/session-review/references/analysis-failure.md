# 振り返りの準備の失敗

`agent-toolkit:session-review`の`SKILL.md`「工程」の工程1で`atk run-script session-review-prepare`が失敗した場合の扱いを定める。

診断が一時原因を示す場合だけ同じ入力で再実行する。同じ診断で再失敗するか一時原因でなければ、`agent-toolkit:wi-standards`に従い、セッションID・失敗事象・原因・解除条件・再現手順を持ち、再開工程を「そのセッションをresumeしてsession-reviewを再実行する」とする欠陥AWIを登録する。transcriptパスは記録せず、全記録をメインのコンテキストへ読み込んで分析しない。
手動起動では分析未完了を報告できる。`agent-toolkit:completion-report`から起動した場合は、分析未完了と欠陥AWIを同スキルへ返し、元作業はそのまま完了させる。

OpenCodeの入力は`--opencode-session-id`、保存先の明示は`--opencode-db`で指定する。
DBまたはセッションが別ホストにだけ存在する場合は、同じセッションIDと再開工程を保持し、記録を持つホストで準備・分析を進め、結果を報告する。
未対応schemaや破損記録は取得失敗として扱い、空の文書や別のセッションの分析で完了を代用しない。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「工程」` | 判断 | `section:agent-toolkit:*/skills/session-review/SKILL.md#「工程」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
