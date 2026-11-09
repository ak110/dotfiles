# レーン計画の起草と再開：再開

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `../../wi-standards/references/cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 旧形式WIの節を現行の責務へ対応付ける前 | `../../wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `finish-session-observation.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session-observation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/lane-integration-subagent-finalization.md` | 判断 | `read:agent-toolkit:*/share/lane-integration-subagent-finalization.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `run-lanes-completion.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes-completion.md` |

## 再開

再開位置が計画ファイルの絶対パスである場合は、`## 概要`、`## 変更履歴`、`## 要件・外部仕様`、`## 進捗ログ`、および同一stemを持つレビュー指摘管理表の応答を確認する。
計画本文と管理表の応答に相違がある場合は管理表の応答を現在の確定情報とし、進捗ログの最終行が示す位置から既存の計画を引き継いで再開する。

`再開位置: 計画なし`の場合は、引き継ぎ記録からAWI集合、採否、検証コマンドを取得し、完了済みコミットを`git merge-base <専用branch> <ベースbranch>`および`git log <その結果>..<専用branch>`によって特定する。
レビュー指摘管理表も確認した上で、最後に完了した工程の次から作業を再開する。
計画の新規作成は行わず、コミット済みの範囲を再実装することも避ける。

選定結果の`再開位置`が`反映後の観測だけが残る`で始まる項目、および進捗ログの最終行が「反映後の観測だけが残る」と示している再開では、既存の実装コミットの再実装は行わず、観測のみを実施する。

1. AWI本文末尾の`## 反映後の観測の再開記録`から、`実装commit`、`残る完成条件`、`観測手段`（起動条件と根拠の所在を含む）、および`計画`の値を確認する。
   `~/.claude/plans`に該当の計画が存在しない場合は、`atk plans checkout <private-notes/plans/からの相対パス>`によって取得する。
   起動条件が不足している場合や変化が認められる場合は、`finish-session-observation.md`「延期adoptと反映後の観測」に従って実際の実装や設定から情報を補い、実行可能性を確定させてから認可と操作内容を判断する。
2. 残りの完成条件を、記録された手順に従って新規プロセスから観測し、その結果と報告時点からの差分を進捗ログ（`計画`が`計画なし`の場合は引き継ぎ記録）へ追記する。
   引き継ぎ記録には、観測のみの再開であること、取得した計画のパス、元の専用worktreeの回収状況も記録する。
   `計画なし`の項目については、再開記録の`実装commit`を検収済みの実装として引き継ぐ。
   取得した計画は`atk plans commit`による保存は行わずに`~/.claude/plans`に保持し、保存は`../../../share/lane-integration-subagent-finalization.md`「計画最終化」の段階で一度だけ行う。
3. 観測で完成条件を満たさなかった場合も再実装せず`マージなし`とする。
   未達条件、操作と結果、観測ログの保存先を進捗ログ（計画なしなら引き継ぎ記録）と返却メッセージへ記す。
   終了区分は`終了しない`とし、`run-lanes-completion.md`「採否の確定と終了区分」に従う。
4. 直接観測できない場合は、不足する反映状態と観測手段を確かめる。
   残存プロセスや環境状態が阻害する場合は、生成元、起動元、起動契機を実態（親子関係、起動時刻、ログ、実装など）から調べ、未確定事項も記す。
   OSユーザー名だけを生成元の根拠にしない。
   不足を「続行できない理由」として委譲元へ報告し、手順5へ進まずその観測を保留する。
   必要な入力・反映状態が整ったら手順1から再開する。
5. 直接観測した結果を点検した場合だけ、統合区分を`マージなし`として`lane-planning.md`「計画検査完了の通知」を送る（計画なしなら`計画ファイル: なし`）。
   追加WIの残工程を続け、全計画の実装要否が確定したら統合指示を待つ。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レーン計画の起草と再開の基準を適用する前 | `lane-planning.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lane-planning.md` |
