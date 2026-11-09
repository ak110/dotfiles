# エージェント宛てメッセージ

本書では、hookやその他の生成元がエージェントへ直接渡すメッセージの記述言語、機械生成テキストの識別方法、およびhook通知の整形ルールを定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-block-warn.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `program-messages.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/program-messages.md` |

## メッセージの記述言語

エージェントへ直接渡す出力（`reason`、`additionalContext`、終了コード2のstderrなど）は日本語で記述する。
日本語以外の文章が会話に混ざると、その後のモデルの応答言語が影響を受けるためである。
原本ファイル（`CLAUDE.md`など）の章名、節名、キーワードは、将来の名称変更に追従しやすくするため原本の表記通りに引用する。

## エージェント宛てメッセージの標識

機械が生成したテキストには、ユーザー自身の発話と区別できるように明確な境界、生成元、通知種別を設ける。
メッセージ内にユーザー入力を転載する場合も個別に識別できるようにし、機械が生成した説明文をユーザーによる認可として扱わないようにする。
エージェントへ届かないフィールドや、追加本文のない通常の許可応答は本規定の対象外とする。

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 通知本文の整形に関する判断・作業の前 | `claude-hooks-messages-format.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-messages-format.md` |
