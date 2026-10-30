# 振り返りの準備の失敗

`agent-toolkit:session-review`の`references/current-session.md`「工程」の工程1において、`atk run-script session-review-prepare`が失敗した場合の取り扱いを定める。

出力された診断が一過性の原因を示している場合に限り、同一の入力で再実行する。再実行しても同じ診断で失敗する場合や、原因が一過性でない場合は、`agent-toolkit:wi-standards`に従って欠陥AWIを登録する。このAWIにはセッションID、失敗した事象、原因、解除条件、再現手順を記載し、再開時の工程を「そのセッションをresumeしてsession-reviewを再実行する」と設定する。transcriptのパスは記録に含めない。また、記録全体をメインエージェントのコンテキストへ直接読み込んで分析することも避ける。
手動で起動した場合は、分析が未完了である旨を報告する。`agent-toolkit:completion-report`から起動した場合は、分析未完了の旨と登録した欠陥AWIを同スキルへ返し、元の作業自体はそのまま完了させる。

OpenCodeを対象とする場合、入力には`--opencode-session-id`を用い、保存先を明示するときは`--opencode-db`を指定する。
データベースやセッションが別のホストにしか存在しない場合は、同一のセッションIDと再開工程を引き継ぎ、その記録が存在するホスト上で準備と分析して結果を報告する。
未対応のスキーマや破損した記録は取得失敗として扱う。空の文書を作成したり、別のセッションの分析結果を流用したりして完了扱いにすることは避ける。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `current-session.md#「工程」` | 判断 | `section:agent-toolkit:*/skills/session-review/references/current-session.md#「工程」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
