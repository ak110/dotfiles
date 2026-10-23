# 回答済みUWIの扱い

本書は`agent-toolkit:user-confirmation-and-report`の細則として、回答済みUWIを元の作業へ反映する手順と、`atk run-skill`が起動したセッションが過去の実行のUWIを扱う手順を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#「状態と依存」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「状態と依存」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#「投入と取得」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「投入と取得」` |

## 回答後の状態遷移

回答の到達は、投入済みで未回答のUWIを保持している間、`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`が定める経過時間起動の各回で`atk wi`により確認する。

`agent-toolkit:process-wi`の起動中は`agent-toolkit:wi-standards`「状態と依存」の手順を適用する。回答済みUWIは`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」へ渡す。それ以外で回答済みUWIの通知を受け取ったエージェントは、自セッション（委譲先を含む）が投入したUWIだけを扱う。他のセッションが投入したUWIは、ユーザーが働きかけていない自動の通知では読まずに無視する。そのUWIは投入したprocess-wiの実行かその次の実行の選定工程が扱い、無関係なセッションが処置を検討するとユーザーへの往復が生じるためである。ユーザーが現在のセッションへ回答を告げたUWIは明示指示として扱う。WIの投入前と着手前に回答済みUWIとの両立を確かめる工程は`agent-toolkit:wi-standards`「投入と取得」に従う。

標準2択の事後承認型UWIへの肯定回答後の遷移は`agent-toolkit:wi-standards`「状態と依存」に従い、同じUWIへの再度の`adopt`や是正処理をしない。問題を示す回答と事前承認型UWIは、次を同じ作業の連続した工程として完了する。

1. 質問と回答を取得する。ユーザーが回答を告げたUWIは`atk wi show <UWIファイル名> --pull`を使い、強制同期と未到達時の再取得は`agent-toolkit:wi-standards`「投入と取得」に従う。強制同期後も未回答なら保存側の送信が未完了の可能性を保ち、既存の再確認契機で同じ公開操作を再実行する。回答を得てから暫定判断との差分を確定する。通常の定期確認には`atk wi show <UWIファイル名>`を使う
2. 回答を元の作業へ反映する。回答済みUWIはUWIのまま扱い、別の作業要求が明示された場合だけ独立したAWIの要否を判定する
3. 反映が成立したUWIを`atk wi adopt <UWIファイル名>`で終端する。回答が質問を退けた場合は`reject`を使い、理由を回答と対応付ける
4. そのUWIが指す保留中の元項目を`atk wi unhold <元項目ファイル名>`で`inbox`へ戻し、他に続行できない理由が無ければ元の処理を再開する
5. 元の`agents_server` sessionを保持している場合は同じsessionへ回答を配送して再開し、保持していない場合は計画の進捗ログから未完了工程だけを再開する。計画が無い場合は、保留時に`atk wi edit --append`で元AWI本文へ追記した解除条件と再開工程を読む

状態変更コマンドの成功報告と警告の不在を各遷移の完了根拠とし、途中で失敗した場合は未完了の遷移から再開する。

## `atk run-skill`の過去の実行のUWI

`atk run-skill`が起動したセッションは、作業の前に`atk wi show --all --type=uwi --state=active --source=run-skill --target-repo=<対象リポジトリの絶対パス>`を実行する。frontmatterの`scope`が自分のスキル名と一致する項目は、同じ作業の過去の実行が投入したUWIとして扱う。`atk run-skill`の各回は同じ対象リポジトリで同じスキルを繰り返す一連の作業であり、次回の`atk run-skill`のセッションが「次の実行」に当たる。このため「回答後の状態遷移」の他セッションのUWIを無視する扱いは、ここでは外す。

- 回答済みの項目には「回答後の状態遷移」の手順1から3を適用し、承認された対応はそのセッションで実施する（元項目と保持中の`agents_server` sessionを持たないため手順4と5は当たらない）
- 未回答の事前承認型UWIは保留のまま残す。同じ対応が必要になった場合は新しいUWIを投入せず、対応を実施しないまま、報告用UWIの`### 未達・持ち越し`へ実施しなかった対応と待っている既存のUWIのファイル名を書く
- 未回答の報告用UWIは各回の報告として残す
