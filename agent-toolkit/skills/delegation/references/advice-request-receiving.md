# 助言依頼の受領

本書は、委譲先から助言依頼（`助言依頼:`で始まる通知）を受け取った委譲元が、委譲先の経過を読んで助言を返す手順を定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲先の会話の抜粋を取得する前 | `../../review-session/references/session-records-conversation.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/session-records-conversation.md` |
| 助言を返すか担当を巻き取るかを選ぶ前 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` |
| 委譲先の停滞を判定する前 | `waiting-stalls.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-stalls.md` |

## 手順

助言依頼は、委譲先を起動した直上の委譲元の`atk agents wait`の`notices`だけに届く。
受け取った委譲元が自分で扱い、さらに上位への中継はしない。

1. 委譲先の直近の会話の抜粋を、上限付きで取得して読む。
   依頼内容は委譲元が既に持っているため、抜粋は委譲先の直近の経過に限る。

   ```text
   atk run-script session-review-prepare -- --agents-session-id <委譲先のsession_id> --conversation-only --conversation-window
   ```

   出力の`conversation_path`が抜粋の保存先である。
   直近の件数やバイト上限の指定は`../../review-session/references/session-records-conversation.md`「会話の少量取得」に従う。
   委譲先の記録全文は読まず、抜粋で足りない箇所だけを`atk agents show`などの既存の詳細照会で補う。
2. 助言を返すか、担当を巻き取るかを、既存の`continuation.md`「継続と新規起動」と`waiting-stalls.md`「停滞の検知と巻き取り」で選ぶ。
3. 助言を返す場合は、`agents_server`の`send_message`で同じsessionへ送る。
   決めたい事項への結論と根拠を書き、委譲先が持たない事実（上位の要求、他の担当の成果など）があれば添える。

担当と成果の責任は委譲先に残る。
委譲先が助言と矛盾する観測を返した場合は、その観測を確かめてから助言を改める。
