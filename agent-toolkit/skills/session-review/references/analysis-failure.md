# 振り返りの準備の失敗

`agent-toolkit:session-review`の`SKILL.md`「工程」の工程1で`atk run-script session-review-prepare`が失敗した場合の扱いを定める。

診断が一時原因を示す場合だけ同じ入力で再実行する。同じ診断で再失敗するか一時原因でなければ、`agent-toolkit:wi-standards`に従い、セッションID・失敗事象・原因・解除条件・再現手順を持ち、再開工程を「そのセッションをresumeしてsession-reviewを再実行する」とする欠陥AWIを登録する。transcriptパスは記録せず、全記録をメインのコンテキストへ読み込んで分析しない。
手動起動では分析未完了を報告できる。`agent-toolkit:completion-report`から起動した場合は、分析未完了と欠陥AWIを同スキルへ返し、元作業はそのまま完了させる。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「工程」` | 判断 | `section:agent-toolkit:*/skills/session-review/SKILL.md#「工程」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
