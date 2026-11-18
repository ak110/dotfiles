# 確認・報告の手段と認識合わせ：起動案内と進捗通知

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| セッション記録を復元・進捗・操作の観測へ使う前 | `../../../share/session-conversation-extraction.md` |
| 本資料の基準で判断する前 | `../SKILL.md` |
| `予定`の改善点を確定して完了報告へ記載する前 | `agent-toolkit:report-completion/SKILL.md` |
| 進捗のセルフチェック通知を受けて点検する時 | `../../delegation/references/waiting-and-monitoring-progress.md` |

## 起動案内と進捗通知

セッション開始時、`clear`実行後、および会話圧縮後の最初の実ユーザー発話に対して、`agent-toolkit:user-confirmation-and-report`の起動を案内する。
また、不具合の有無・原因・修正方針を回答や確認に含める場合は、診断前に`agent-basics:bugfix`を起動するよう案内する。
両ホストにおいて会話文脈ごとに1回配送し、機械的な注入処理では配送待ち状態を消費しない。
発話内容と現物の突き合わせ作業は、同スキルの「原文の保持と取り込み」および「確認要否の判定」に従う。
固定手順を再注入する契機は、上記の発話の時点に限る。

案内の対象となる通常発話には、ユーザーが入力したスラッシュコマンドを含める。
ハーネスが挿入した通知や機械注入によるターンは対象外とする。
生成された注記は単一の`additionalContext`に統合し、Codex payloadにおいても同様に返却する。

進捗のセルフチェック通知（`source="progress-self-check"`）は、前段の案内と別に、呼出主体ごとに30分間隔でPostToolUseとUserPromptSubmitが返す。
経過時間と、`agent-conduct-details.md`「進捗点検」節への参照を示す点検の契機であり、前段の固定手順の再注入とは別の扱いとする。
通知生成時の日時と、この主体を初めて観測した時点からの経過を示す。
報告に使う現在時刻は`agent-basics:writing-standards/references/quality-principles-facts.md`に従って取得し、通知の日時と経過の起点は、それぞれ通知の生成時点とこの主体の初回観測時点として扱う。
長い委譲の往復の間も点検の契機を届けるため、この通知は機械注入のターン（定期再確認を含む）にも付ける。
実ユーザーの発話の有無や無操作期間の長さによらず、前回通知から30分以上たった観測で通知する。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| `予定`の改善点を確定して完了報告へ記載する前 | `agent-toolkit:report-completion/SKILL.md` |
| 進捗のセルフチェック通知を受けて点検する時 | `../../delegation/references/waiting-and-monitoring-progress.md` |

通知を受けた主体の点検の手順、記録の要否、報告と通知は`agent-toolkit/share/agent-conduct-details.md`「進捗点検」に従う。
比較に使う情報は`../../delegation/references/waiting-and-monitoring-progress.md`「成果物側の観測」に従う。
委譲元は点検ごとの一律の受領返答や再照会を求めず、ユーザーへの送達は`communication-methods-delivery.md`「手段の選択」に従う。

ユーザーが入力した文章は、Claude Codeの`AskUserQuestion`の回答（提示した`label`と一致しない`answers`の値、`response`、`annotations`の`notes`）として`PostToolUse`にも届く。
発話内容を判定材料とする注記は両hookで同一の定義本文を用い、原因・対象・対処の変化に応じた共通の新規性判定に基づいて配送する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 確認・報告の手段と認識合わせ：手段の選択の基準を適用する前 | `communication-methods-delivery.md` |
