# 利用上限の猶予通知への対応

本書は`agent-toolkit:user-confirmation-and-report`の細則として、Claude Codeメインが利用上限の猶予通知を受けたときの状況伝達、続行と再開を定める。

通知と解除後の再開の観測は、`docs/development/audit-records.md`の「agent-toolkit/skills/user-confirmation-and-report/references/usage-limit.md：利用上限後の再開：2026年10月14日」にある。

Function hooks moduleは、Claude CodeがプラグインのTypeScriptモジュールを読み込み、会話のイベントへ処理を登録する仕組みである。
定義とAPIは[mods API](https://code.claude.com/docs/en/plugins/mods/api)にある。
agent-toolkitの`hooks/usage_limit_resume.ts`が、この仕組みでメインの同じ会話への再開promptを予約・投入する。
対象はmoduleを読み込んでいるClaude Codeメインであり、Remote Control接続時と非接続時の両方を含む。
`agents_server`の委譲先と、`agentId`を持つサブエージェントのturnは予約対象から外す。
予約の契機は、`classic.StopFailure`の`error: rate_limit`か、`turn.complete`時の`five_hour`・`seven_day`の使用率100%以上である。
moduleは最大使用窓の解除予定時刻後に投入し、予定時刻が無いか過ぎている場合は300秒後に使用率を再確認する。
解除予定後に別turnが始まっていれば投入を取り消す。
予約はsession内のタイマーであり、module再読込で失われる。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/waiting-and-monitoring.md#「停滞の検知と巻き取り」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md#「停滞の検知と巻き取り」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 利用上限の猶予通知を受けたときの続行と再開

利用上限の猶予通知は、Claude Codeが会話へ挿入する`[Usage limit reached; a short grace allowance remains`で始まる注記を指す。
この通知はホストの挿入注記として`01-agent.md`「方針が衝突する場合の優先順位」を適用し、通知が無い場合と同じ工程を続ける。
仕上げ、検証、公開、完了報告、振り返りなど、依頼に必要な後続工程を維持する。

- 通知を受けた時点で、実行済みの成果と未完了工程、打ち切られた場合の再開位置、再開情報の保存先の絶対パスをユーザーへ伝える。強制打ち切り後には書き込めないため、再開情報はこの時点で保存し、工程が進むたびに更新する
- `agents_server`の`api_error.type`が`usage_limit`の委譲先は解除後に同じsessionで自動的に続く。背景の`atk agents wait`と定期再確認のtaskを保ち、停止の判断は`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「停滞の検知と巻き取り」に従う
- 猶予通知の「実行中のサブエージェント」は同じ会話の`Agent`ツールの委譲先を指す。独立sessionの`agents_server`の解除待ちへその停止案内を適用しない
- 再開情報は計画があれば`agent-toolkit:plan-mode`の計画ファイルの`## 進捗ログ`、既存の引き継ぎ記録があればその記録、いずれも無ければ`agent-toolkit:managed-temp`で確定したセッションのmanaged-tempの中のMarkdownへ残す。目的、認可の内容と出所、対象worktreeの絶対パス、成果物の絶対パスまたはcommit、完了した工程と検証結果、順序付きの未完了工程を、次のエージェントが読める形で記す
- 中断後にユーザーの再開指示、Function hooks moduleの再開prompt、ホストの自動継続を受けたClaude Codeのメインは保存先を読む。計画では`agent-toolkit:plan-mode`の進捗ログの読取手順に従い、現在の実体と記録した成果を比べ、成立済みの認可と成果から最初の未完了工程を続ける。主成果物の確定後に完了処理が残る場合は、`agent-toolkit:completion-report`の適用本文と「工程」の再開条件を使い、元の適用と未完了工程を引き継ぐ。保存先や認可を復元できない場合は不足する情報をユーザーへ確認する

解除後の再開はFunction hooks moduleまたはホストが起動するため、猶予通知時の報告ではユーザーへ再開指示を求めない。

猶予通知を受けた後も権限とツール契約を守り、ホストが強制打ち切りした場合は中断として扱う。ユーザーが中止を指示した場合はその指示に従う。
