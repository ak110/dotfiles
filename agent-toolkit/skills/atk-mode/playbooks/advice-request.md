# 助言の依頼と応答

`agents_server`の通常起動の委譲先が自力で進められなくなった時に、直上の委譲元へ技術的な助言を求め（助言依頼）、委譲元が経過を読んで返す手順書である。
方針: 助言を受けた後も、担当と成果の責任は委譲先に残る（原則7、`agent-basics:writing-standards/references/quality-principles-ownership.md`）。
助言と自分の観測が矛盾する時は、観測を確かめてから従うか改める（原則3、`agent-basics:writing-standards/references/quality-principles-facts.md`）。

## 助言依頼ができる委譲先

起動の本文に`atk-auto`要素の`kind="advice-request"`の通知を受け取った委譲先だけが、助言依頼を出せる。
通知の有無は、`agents_server`が委譲元と委譲先の実モデルの組から決める。
通知が無い委譲先は本書を使わず、`agent-toolkit:atk-mode/playbooks/subagent.md`「確認事項の即時通知」と返却形式だけを使う。

## 助言依頼を出す時機

次のどちらかに当たる時に限る。

- 対処を変えても同じ失敗が続く。
- 担当範囲の技術判断で、複数案の優劣を自力で決めきれず、誤ると手戻りが大きい。

次の事項は、助言依頼の代わりにそれぞれの手段で扱う。

- ユーザーの確認が要る事項は、`agent-toolkit:atk-mode/playbooks/subagent.md`「確認事項の即時通知」で送る。
- 判断・認可・入力・権限の不足は、同書「返却形式の受け渡し」の続行できない理由として返す。
- 調査で確定できる事項は、自分で調べる。

助言のたびに委譲元の文脈へ会話の抜粋と助言が残るため、方針を決める前や完了を宣言する前の確認は、助言依頼の対象から外す。

## 依頼の手順（委譲先）

1. 助言依頼を1件送る。
   環境変数`AGENT_TOOLKIT_OWNER_SESSION`が設定されていれば、`atk agents notify --body`か`--body-file`で送る。
   本文にメタ文字や改行を含む時は、UTF-8のファイルへ保存して絶対パスを`--body-file`へ渡す。
   同じ環境変数が無い時と送信に失敗した時は、`agent-toolkit:atk-mode/playbooks/subagent.md`「確認事項の即時通知」の次の手段を使う。
   `agents_server`の`send_message`とCodexの協調機能の`send_message`は、委譲元への通知の手段から除く。
   本文は`助言依頼:`で始め、決めたい事項、試した対処と結果、自分の案を短く書く（経過は委譲元が会話の抜粋で読むため、再掲を省く）。
2. 助言に依存しない作業が残っていれば続ける。
   残っていなければ、`続行できない区分: owner_input`と、助言依頼への返答を待つ旨の理由を返してターンを終える。
3. 返答は、委譲元が`send_message`で送る同じセッションへの新しい入力として届く。
   待機や照会の操作は省き、届いた入力を受けて同じ担当として再開する。
   助言が自分の観測と矛盾する時は、従う前に矛盾する観測を委譲元へ返す。

## 受領の手順（委譲元）

助言依頼は、委譲先を起動した直上の委譲元の`atk agents wait`の`notices`だけに届く。
受け取った委譲元が自分で扱い、さらに上位へは中継せずに完結させる。

1. 委譲先の直近の会話の抜粋を、上限付きで取得して読む。

   ```text
   atk run-script session-review-prepare -- --agents-session-id <委譲先のsession_id> --conversation-only --conversation-window
   ```

   出力の`conversation_path`が抜粋の保存先で、件数とバイトの上限は`agent-toolkit:atk-mode/references/work-contract.md`「会話の少量取得」で指定する。
   依頼の内容は委譲元が持っているため、記録の全文は読まず、足りない箇所だけを`atk agents show`などで補う。
2. 助言を返すか担当を巻き取るかを選ぶ。
   基準は`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`「継続と新規起動」と`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`「停滞の検知と巻き取り」にある。
3. 助言を返す時は、`agents_server`の`send_message`で同じsessionへ送る。
   決めたい事項への結論と根拠を書き、委譲先が持たない事実（上位の要求、他の担当の成果など）があれば添える。
4. 委譲先が助言と矛盾する観測を返したら、その観測を確かめてから助言を改める。

返す報告に含める項目: 決めたい事項、助言の結論と根拠（委譲元）、助言を受けて変えた作業と残る作業（委譲先）。
