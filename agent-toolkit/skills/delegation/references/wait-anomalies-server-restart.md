# 待機の異常時の扱い：MCPサーバーの再起動後

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 待機の呼び出しが拒否された時、またはclassifierが判定不能を返した時 | `../../user-confirmation-and-report/references/permission-denial.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` |
| 必須操作を報告する前 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |

## MCPサーバーの再起動後

1. 元担当の状態を確かめる。
   MCPサーバーが再起動した後も、セッション登録簿を通じて同じ`session_id`が遅延解決される。
   ただし、結果の観測や会話の再開に使用できるのは、終了が確定した記録に限られる。
   正常に停止したサーバーは、実行中だったターンを`interrupted`として公開する。
   強制終了によって`running`のまま残されたCodexの記録は、`show`、`send_message`、`kill`の各操作で照会した際に終了状態として引き継がれることがある。
   この引き継ぎが行われる条件は、いずれのサーバーもそのセッションを保持しておらず、ターンを実行していた委譲先CLIのプロセスがすでに終了しており、Codexのスレッド記録がターンの終了を示していることである。

   交代前の旧サーバーが応答しないまま残存している場合や、旧サーバーの管理下で委譲先CLIが作業を継続している場合は、所有サーバーを失った実行中セッションとなる。
   この状態は、`show`または`atk agents show`の出力において`status`が`running`かつ`ownership`が`owner_lost`となっていることで識別できる。
   あわせて`recorded_processes`（所有サーバーおよび委譲先CLIのPIDと生存状態）、`owner_root_session_id`、`stranded_notice_count`も確認する。
   この状態のセッションに対して`send_message`を実行すると、`error.recovery=owner_lost`で拒否される。

2. 作業を継続させる場合は、`atk agents wait`で処理の終了を待つ。
   旧ルートに残っていた委譲先からの通知は、新サーバーの`atk agents wait`や`show`、`send_message`、`kill`の応答を通じて回収され、返却される。

3. 続行のための追加指示が必要な場合は、まず同じセッションに対して`kill`を発行して委譲先CLIを停止させてから、同じ`session_id`宛てに`send_message`を送信する。
   `kill`は実行中のターンを中断する。
   このとき、新たな`start`で後任セッションを起動しない（旧委譲先CLIが作業ツリーへ書き込みを続け、書き込み主体が重複してしまうためである）。

4. `error.recovery=turn_unobserved`: ターンの終了が未確定な状態を示す。
   同じ作業を新しい`start`でやり直すことは避け、まだ実行中である可能性を添えて委譲元へ報告する。

5. `missing`、`unreadable`、`no_resume_info`: 委譲先CLIのプロセスが残存していないことを確認したうえで、そのセッションが失われたものとして処理する。
   起動時の応答や記録からPIDを取得できる場合はプロセスの生存を確認し、生存しているなら終了を待つ。
