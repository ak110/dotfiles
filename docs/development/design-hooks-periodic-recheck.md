# 定期再確認の装着と回収の設計記録

[設計記録の索引](design.md)から参照する、定期再確認の装着と回収の記録である。

## Function hooks moduleの個別設計

### `start`の処理の中での定期再確認の装着

2026年10月7日、`agents_server`の`start`での定期再確認の装着を、モデルが起動前に行う手順からClaude Codeのmod（`agent-toolkit/hooks/periodic_recheck.ts`）が`start`の処理の中で行う方式へ改めた。
ユーザーが`<役割名>.parent.md`による起動までの手順の多さを指摘し、`start`の後の自動化を求めたためである。
装着が`allow`を要する`CronList`・`CronDelete`・`CronCreate`は配布原本`share/claude_settings_json_managed.json`の`permissions.allow`へ置く。
auto modeでは一覧に無いツールの判定が`ask`となり装着を見送ることがある。
`pytools/claude_hook/distribution_test.py`がmodの`tool.check`から抽出した全ツール名と許可一覧の対応をチェックする。
modはメインが定期再確認のtaskを持たない場合に一覧取得・作成・削除の許可を確認し、`atk wait-schedule`と同じcron式で`CronCreate`を作成して、task IDを`start`の結果の後に届ける。
装着できない場合は、理由と許可・公開状態の確認、完了通知による待機を案内する。
装着は成功した`start`ごとに評価し直し、許可や公開状態が回復した後の`start`で自動作成する。
装着失敗の通知は会話（`$.session.id()`）ごとに最後に届けた本文と比べ、同じ本文なら`start`の結果へ加えない。
一覧取得、`atk wait-schedule`の取得、`CronDelete`の能力確認、作成の許可判定・拒否・失敗と装着の例外は全て同じ通知本文の生成を経由するため、どの分岐でも理由が変わった時点の通知が届く。
作成に成功した場合と既にtaskを持つ場合は既往を破棄し、正常化の後に生じた失敗は再び届ける。
既往はmodのプロセス内の変数に限り、再読込後の最初の失敗は届ける。
会話を識別できない場合は抑止しない。
成功した`start`ごとに同じ警告を追加すると、許可が回復するまで会話へ同じ本文が積み上がり、読む費用だけが増えるためである。
通常回収の通知（`session_exit.ts`）も同じ失敗の反復を抑える。
モデルが手順を守らなくても装着できるようになり、起動前の`atk wait-schedule`、`ToolSearch`と`CronCreate`の呼び出しが不要になる。
「委譲手段の選択と待機の装着の手掛かり」が`start`の公開説明に置いた手掛かりは、装着できなかった場合と`Agent`ツールの委譲の手順への案内へ改めた。
定期promptの共通本文は`agent-toolkit/agent_toolkit/_common/periodic_recheck.py`だけが持ち、`atk wait-schedule --format json`がcron式とともに出力する。
modとモデルが自ら装着する手順は、どちらもこの出力を使う。
当初は本文をTypeScriptの`periodic_recheck_prompt.ts`に置いたが、modを読み込まない環境でモデルが自ら装着する手順は、plugin rootを解決してTypeScriptの文字列を連結する必要があった。
メインのシェルがplugin rootを解決できなかった事象と同じ前提を必要とするため、シェルから同じ定義へ到達できるPythonの出力へ移した。
定義元を1つに保つ目的は変えていない。

装着はメインに限り、`Agent`ツールのサブエージェント（`agentId`を持つ呼出主体）へは装着も通知もしない。
2026年10月7日、`agents_server`で起動したClaude Codeのセッションで次を観測した。
`Agent`ツールのサブエージェントが`CronList`を呼ぶと、親が作成したtaskが一覧に現れた。
前景のサブエージェントが`CronCreate`で作成した1回限りのtaskは、サブエージェントの終了後の発火時刻に親の会話へpromptとして届いた。
背景で起動したサブエージェントには`CronCreate`が公開されなかった。
このため、modがサブエージェントのために作成したtaskは、サブエージェントの待機を再確認せずに親の会話へ定期promptを届け、親の保有の判定もそのtaskを自身のものに数え得る。
サブエージェントは装着の有無によらず完了通知で待機を解くため、装着をメインに限っても待機が成立しなくなる主体は無い。
`agents_server`で起動した委譲先は独立したセッションのメインとして`agentId`を持たず、装着の対象に残る。

共通本文はそのセッションに固有の経過時間起動の義務（定期報告、cooldown解除、期限監視など）を含めない。
義務がある場合は、taskを持つメインが義務ごとの測定コマンドと判定閾値を共通本文の終了タグの前へ加えたpromptで同じtaskを作成し直す（`CronDelete`の後に同じ1行目の標識で`CronCreate`する）。
義務とコマンドと閾値を持たない定期promptで定期報告が3時間以上欠落した2026年8月の事象（`incidents-workflows.md`）の対策はコマンドと閾値をpromptへ接続することだった。
modによる共通本文の装着とこの対策を両立させるため、共通本文の装着はmodが行い、セッション固有の行の追加はtaskを持つメインが行う。
発火の各回にモデルが記録と規範から義務を列挙し直す方式は、事象で失敗した方式と同じであるため採らない。
共通promptは既存のPythonの`auto_message`で生成主体・用途を持つ開始タグと終了タグに囲む。
modが生成する通知は純粋な`hooks/automated_message.ts`を共用し、属性だけをエスケープして本文を保つ。
消費側の生成関数から実際の送出値を得る契約試験は、Pythonの共通受信判定へTS側の値も渡し、閉じた機械入力と通常発話を区別する。
modごとに包装を複製する案は、片方だけの境界欠落を再び生むため採らない。
`hooks.json`の`modules`は1件だけを受理し、ツール名の条件を持たない同じイベントのhookは1回しか登録できない。
このためmodのhookは`agent-toolkit/hooks/register.ts`から登録し、本体を別モジュールに置く。
通常の回収は`session_exit.ts`の既存の`turn.complete`へ集約する。
`/goal`の評価はStopの時点で行われ、未達ならターンを継続させるため、`turn.complete`の回収は評価より後にしか動かない。
評価はStop入力に残る定期再確認のtaskを未完了と判定する。
このため`/goal`を設定したセッションでは、メインが全待機の回収後、最終ターンを閉じる前に同じ範囲のtaskを削除する（`agent-toolkit/share/rules/rules-main.claude-code.md`「ターンの終え方」）。
2026年11月8日〜10日に`/goal`を設定して自動起動した3つのセッションが、担当工程と報告の送達を終えた後も、残ったtaskを理由に目標の評価で継続された事象への対処である（うち2つでは`…`だけの最終応答も理由に挙がった）。
modの回収をStopの時点へ移す案は、modが登録できるイベントにStopの時点のものが無いため成立しない。
`turn.complete`へ置く後始末が、Stop入力の`session_crons`・`background_tasks`・`last_assistant_message`に未完了と読める状態を残す場合は、目標の設定下でその状態を評価より前に解消する主体と手順を別に定める。
PythonのStopが`transcript_path`・`session_id`・`background_tasks`・現在のセッション状態を既存の`is_pending_async_work`へ渡し、同じ会話のmodへ1回限りの判定を渡す。
Stopごとに前の判定を無効にし、modは使用前に消費済みへ変える。
session.startと新しいturn.startでも初期化して、中断したターンの判定の持越しを防ぐ。
読取不能・欠落・破損と継続を求めたStopから全終了を推定しない。
これはStop入力の受渡しであり、待機対象の別台帳は持たない。
通常回収の適用前には`$.tool.list()`で現在の会話へ`CronList`が公開されていることを確認する。
CronListを持たない軽量探索などでは回収を適用せず、正常な調査結果の後に通知promptを投入しない。
tool.checkは公開済みツールの許可判定だけへ使う。
所有taskを一覧で確認した後の削除拒否・失敗は対象IDを含めて通知する。
modは全待機対象の終了と結果回収後に`CronList`で役割標識とIDを再解決し、共通promptと一致する非durableのtaskを`CronDelete`する。
追加義務付きのtaskは共通promptとの差分で保護し、既存のセッション終了時回収へ渡す。
モデルへの削除ツールの公開とは独立して動作する。
拒否・失敗時はtaskを保持し、同じ会話へ理由と次の操作を届けて次の有効な終了判定で再評価する。
同じ失敗の通知がターンを再発生させ続けることは抑える。
作成と回収を同じmodへ集約することで、通常の回収をモデルの呼び忘れと能力差から独立させる。
削除能力だけを作成前に確認して通常回収をモデルへ残す案は、回収の実行契機が分離したままになるため採らない。
却下した代替案は3つある。
PostToolUseのhookがcron式と`CronCreate`の引数を通知する案は、モデルの`ToolSearch`と`CronCreate`の呼び出しが残り、装着にモデルの手順遵守を必要とする点を除けない。
`start`の応答へcron式を同梱する案は、サーバーが呼出主体のrequest bucketとホストの`CronCreate`の有無を知らず、Codexへも同じ応答が返るため成立しない。
modが`$.clock.every`と`$.prompt.submit`で定期再確認そのものを行い`CronCreate`を使わない案は採らない。
`CronCreate`を前提とする既存の契約（Stopの非同期待機の判定、taskの再利用と削除、`/exit`前の削除）を再設計する必要があり、起動前の手順を減らす要求に対して変更が大きい。
