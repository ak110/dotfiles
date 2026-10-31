# 回答済みUWIの扱い

本書は`agent-toolkit:user-confirmation-and-report`の細則として、回答が得られたUWIを元の作業へ反映する手順と、`atk run-skill`で起動したセッションが過去の実行で作成されたUWIを処理する手順を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/waiting-and-monitoring.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#「状態と依存」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「状態と依存」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#「投入と取得」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「投入と取得」` |
| run-skillのUWIを保存・取得する起動単位を判定する前 | `../../wi-standards/references/origin-and-approval.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval.md` |

## 回答後の状態遷移

投入済みで未回答のUWIを保持している間は、`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`に定める定期チェックのタイミングごとに、`atk wi`を実行して回答が届いているかを確認する。

`agent-toolkit:process-wi`の実行中は、`agent-toolkit:wi-standards`「状態と依存」の手順に従う。回答が得られたUWIは`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/selection-procedure.md`「処理対象WIの追加」へ渡す。それ以外の場面で回答済みUWIの通知を受け取った場合は、自セッション（委譲先を含む）が投入したUWIだけを処理対象とする。ユーザーからの明示的な指示がない自動通知で届いた他セッションのUWIは、読み込まずにそのまま置く。他セッションのUWIは、それを投入した`process-wi`の実行時か次回の選定工程で処理されるためであり、無関係なセッションが対応を検討すると余計な確認の往復が生じる。ユーザーが現在のセッションで直接回答を伝えてきたUWIは、明示的な指示として受け取って処理する。WIの投入前や着手前に回答済みUWIとの整合性を確かめる手順は、`agent-toolkit:wi-standards`「投入と取得」に従う。

標準的な2択の事後承認型UWIに対して肯定的な回答が得られた場合の遷移は、`agent-toolkit:wi-standards`「状態と依存」に従う。この場合、同じUWIに対して再度`adopt`を実行したり、追加の是正処理を行ったりする必要はない。問題の指摘を含む回答や、事前承認型UWIへの回答を受け取った場合は、次の手順を一連の作業として順番に完了させる。

1. 質問と回答の内容を取得する。ユーザーが回答を伝えてきたUWIは`atk wi show <UWIファイル名> --pull`で取得する。強制同期や未到達時の再取得は、`agent-toolkit:wi-standards`「投入と取得」に従う。強制同期後も未回答のままであれば、送信側の同期がまだ完了していない可能性を考慮し、後続の定期確認のタイミングで同じ公開操作を再度試みる。回答の取得を確認した後に、事前の暫定判断との差分を確定する。通常の定期確認には`atk wi show <UWIファイル名>`を用いる。
2. 反映の作業を始める前に`atk wi start-processing <UWIファイル名>`でUWIを`processing`へ移し、回答を処理中であることをキュー上と`atk serve`の画面へ表す。回答を保存しただけで反映を始めていないUWIは`inbox`のまま残す。続けて回答内容を元の作業へ反映する。回答済みUWIはUWIとして扱い、別の作業要求が明示された場合のみ、独立したAWIを起草するか判定する。
3. 反映を完了したUWIは`atk wi adopt <UWIファイル名>`を実行して終了させる。回答が提案や質問を退ける内容だった場合は`reject`を使い、退けた理由を回答内容と対応づけて記録する。
4. そのUWIを`depends_on`に持つ元項目は、UWIの終了で依存が解消する。元項目が依存している未回答のUWIが他に無ければ、中断していた元の処理を再開する。旧運用で`hold`に置かれた元項目は、回答を反映した後に`atk wi unhold <元項目ファイル名>`で`inbox`へ戻す。
5. 元の`agents_server`セッションが残っている場合は、そのセッションへ回答を届けて処理を再開する。セッションが残っていない場合は、計画ファイルの進捗ログを確認し、未完了の工程から再開する。計画ファイルが存在しない場合は、依存の保存時に`atk wi edit --append`で元AWIの本文へ追記しておいた再開手順を読み取って進める。

各遷移が完了したと判断する根拠には、状態変更コマンドの正常終了と警告が出ていないことを用いる。途中で失敗した場合は、完了していない遷移からやり直す。

## `atk run-skill`の過去の実行のUWI

一般の確認UWIと報告用UWIは、`../../wi-standards/references/origin-and-approval.md`の保存規則で求めた起動単位と、保存済みscopeが一致する場合に取り込む。モード語以外の自由記述が異なっても同一起動単位として扱う。

batchモードでは旧保存データのscope "agent-toolkit:session-review-batch"も同じ起動単位として読み取る。通常モードのscopeとは区別する。

`atk run-skill`で起動したセッションは、作業を開始する前に`atk wi show --all --type=uwi --state=active --source=run-skill --target-repo=<対象リポジトリの絶対パス>`を実行する。frontmatterの`scope`が自身の起動単位（スキル名とモードの組。通常起動はスキル名）と一致する項目は、同じ作業の過去の実行で投入されたUWIとして扱う。`atk run-skill`は同じ対象リポジトリに対して同じスキルを反復実行する一連の作業であり、次回起動する`atk run-skill`のセッション自身が「次の実行」に該当する。そのため、「回答後の状態遷移」で定めた他セッションのUWIを無視する規定は、この場面では適用しない。

- すでに回答が得られている項目には、「回答後の状態遷移」の手順1から手順3までを適用し、承認された対応をそのセッション内で実施する（元項目や保持している`agents_server`セッションは存在しないため、手順4と手順5は対象外となる）。
- まだ回答のない事前承認型UWIは、未回答のまま維持する。同じ対応が再び必要になった場合でも新規のUWIは投入せず、対応の実施を見送る。そのうえで、報告用UWIの`### 未達・持ち越し`に、見送った対応内容と回答待ちである既存UWIのファイル名を記録する。
- まだ回答のない報告用UWIは、各実行回の記録としてそのまま残す。
