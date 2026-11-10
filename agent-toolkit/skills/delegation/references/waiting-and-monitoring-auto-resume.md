# 待機と観測：完了通知を待ってターンを終える場合

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| セッション記録を復元・進捗・操作の観測へ使う前 | `../../review-session/references/session-records-conversation.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/session-records-conversation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-code-runtime-receiving.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-receiving.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-code-runtime-waiting.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/claude-code-runtime-waiting.md` |
| Claude Codeのバックグラウンドタスクの起動形を判断する時 | `waiting-claude-code.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-claude-code.md` |
| 停滞の検知と巻き取りへ進む時 | `waiting-stalls.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-stalls.md` |
| 拒否またはclassifierの判定不能で待機を開始できない時 | `wait-anomalies-refusal.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-refusal.md` |
| 通知を受け取れない担当を新規起動へ切り替える前 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` |

## 完了通知を待ってターンを終える場合

1. 再開できる待機だけでターンを終えるため、自動再開の能力があることと、今回の未回収対象の追跡・結果保留・継続が成立していることを分けて確認する。
   待機の未起動や過去の継続消費を、対象の終了として扱わない。
   今回の再開が保証されていなければ許容された待機を続け、ホスト制約で続行できない場合は`wait-anomalies-refusal.md`「待機を開始できない場合」に従って観測主体を引き継ぐ

2. 1回のブロッキング待機（`atk agents wait`またはそれを起動したバックグラウンドタスク）は、発行時点で委譲元が保持していた起動中セッション全体を、終了ステータスを返すまで所有して管理する。
   定期再確認では、所有されている対象の観測をブロッキング待機に委ね、所有されていない対象については記録側の状態や完了通知、および経過時間起動の義務のみを確認する。
   完了通知の不着が検出された対象や、待機手段が終了ステータスを返さずに失敗した対象は、所有されていないものとして扱う。
   上限到達により`running`を返したブロッキング待機の再発行は`waiting-and-monitoring-intervals.md`「待機区間の構成」に従う

3. 定期再確認は、Claude Codeでは`claude-code-runtime-waiting.md`「待機中の定期再確認と背景転換」に従い、委譲先やバックグラウンドタスクを実際に起動したセッションにのみ設定する。
   `start`の結果を受け取った時点でFunction hooks moduleによる定期再確認の設定は完了している。
   自身で明示的に設定する場合も、起動の直前または通知を受け取った直後に設定する（設定までの間隔が空くと、停滞や外部ジョブの終了を検出する主体が存在しない期間が生じるためである）

4. 作成済みで未回答のUWIを保持しているセッションは、経過時間起動の各回で`atk wi`を使って回答の有無を確認し、回答が得られている場合は同じターン内で元の作業へ反映する（この契機がないと、ユーザーが会話を通じて通知するまで作業工程が停止してしまう）

5. 子孫プロセスの完了通知を直接の委譲元以外のエージェントが受け取った場合の中継、再送、巻き取りの手順は、`claude-code-runtime-receiving.md`「完了通知と中継の実行順」に従う

6. 完了通知が届かない場合は`waiting-stalls.md`「停滞の検知と巻き取り」へ進み、成果物ファイル、委譲先が起動した子プロセスのトランスクリプト、および委譲先自身のトランスクリプトの最終更新時刻を実際に取得して確認する。
   終了の判定には実行識別子の直接照会も併用し、委譲先が起動したプロセスがすでに終了していると誤認して、同じ工程に複数のエージェントを誤って接続しないようにする

7. 定時起動の依頼文には、現在の作業状態を正しく記録している情報源（計画ファイル、引き継ぎ記録、作業ツリー、タスク一覧など）から状態を読み直す手段のみを記載し、待機対象ID、成果物の絶対パス、コミット識別子、残工程や完了済み工程そのものは書き込まないようにする（努力目標）

8. 定時起動が発火しても停滞時間の起点は更新されず、停滞判定は実際の観測値に基づいて行う

9. ターンを終了する直前には成果物側の状態を観測し、前回から値が変化していれば、その値と待機を継続する理由をユーザーへ簡潔に説明する（努力目標）。
   稼働状態の照会は、停滞を判定する回と、催促や巻き取りの要否および宛先を確定する回に限って発行する

   Claude CodeのStop時に未配信の完了通知が存在し、通知後の同一セッション再開が保証され、他のagent-toolkitのチェックがブロックを返していない場合は、現在のターンを終了し、通知の配信に伴う次のターンで保存出力と終了状態を受け取る。
   他のブロックが残っている場合に限ってその要因を解消しつつ、結果受け取りに関する警告に従う。
   同一の結果を得るための待機コマンドの重複再発行は避ける。

   完了通知で再開した現在ターンでは、前ターンのStop終了を現在ターンのツール操作禁止と解釈せず、保存出力と終了状態を回収する。
   未終了の担当が残る場合は、有効な待機を継続するか、同じセッションの自動再開が維持されることを確かめる。
   観測を試みた履歴だけでは、現在の待機・継続主体の存在は確認できない。
   ホストが現在の操作を実際に拒否した場合は、その拒否と既存の回復手順に従う。

   待機のみを理由としてターンを終了する際の出力内容は以下のとおりとする。
   自動再開の対象には、自身が`agents_server`で起動した委譲先セッション、Codexの組み込み委譲で起動した子スレッド、およびClaude Codeの通知付きバックグラウンドタスクが含まれ、Codexのcell IDや`exec_command`のsession IDは対象外となる。
   本表の内容を変更する場合は、各エージェントに届くエージェント向け文書（表中の参照先）もあわせて更新する。

   | エージェント | 同じセッションの自動再開あり | 自動再開なし |
   | --- | --- | --- |
   | メイン | 本文を空にする。空の応答をホストが再生成させる場合（Claude Code）は`…`のみを出力する（`share/rules/rules-main.md`「ユーザー向け発話ルール」、`share/rules/rules-main.claude-code.md`） | 公開待機手段で同じターン内に終了状態と結果を回収し、後続工程を完了してから報告する |
   | `agents_server`の委譲先 | `待機中: <実行識別子>`の1行（`share/agents-server-auto-resume.md`、`share/rules/rules-subagent.md`「返却と終了」） | 待機を理由にターンを終えず、`agent-toolkit/share/rules/common/02-agent-operations.md`の「待機と背景実行」に定める前景待機で終了を確定させてから所定の返却形式を返す |
   | `Agent`ツールのサブエージェント | 完了通知で再開する起動形態では`待機中: <実行識別子>`の1行 | 同上の前景待機 |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 待機と観測：待機区間の構成の基準を適用する前 | `waiting-and-monitoring-intervals.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-intervals.md` |
