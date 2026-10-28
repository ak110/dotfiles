# 利用上限の猶予通知への対応

本書は、`agent-toolkit:user-confirmation-and-report`の詳細な規則として、Claude Codeのメインエージェントが利用上限の猶予通知を受けた際の状況報告、作業の継続、および再開手順を定める。

利用上限の通知と解除後の再開に関する観測記録がある。詳細は`docs/development/audit-records.md`の「agent-toolkit/skills/user-confirmation-and-report/references/usage-limit.md：利用上限後の再開：2026年10月14日」に記載されている。

Function hooks moduleは、Claude CodeがプラグインのTypeScriptモジュールを読み込み、会話イベントに応じた処理を登録する仕組みである。
定義とAPI仕様は[mods API](https://code.claude.com/docs/en/plugins/mods/api)にある。
agent-toolkitの`hooks/usage_limit_resume.ts`は、この仕組みを利用してメインエージェントの同一会話に対する再開プロンプトを予約・投入する。
対象はモジュールを読み込んでいるClaude Codeのメインエージェントであり、Remote Controlの接続中および非接続時の双方を含む。
`agents_server`の委譲先や、`agentId`を持つサブエージェントのターンは予約の対象外とする。
予約が行われる契機は、`classic.StopFailure`の`error: rate_limit`が発生した時点、または`turn.complete`時に`five_hour`や`seven_day`の使用率が100%以上に達した時点である。
モジュールは最大使用枠の解除予定時刻を過ぎた後にプロンプトを投入する。解除予定時刻が不明な場合やすでに経過している場合は、300秒後に使用率を再確認する。
解除予定時刻の後に別のターンがすでに開始されている場合は、投入を取り消す。
なお、この予約はセッション内のタイマーとして保持されるため、モジュールの再読み込みによって破棄される。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/waiting-stalls.md#「停滞の検知と巻き取り」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-stalls.md#「停滞の検知と巻き取り」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 利用上限の猶予通知を受けたときの続行と再開

利用上限の猶予通知とは、Claude Codeが会話ログ内へ挿入する`[Usage limit reached; a short grace allowance remains`で始まる注記を指す。
この通知に対しては、ホストの挿入注記として`01-agent.md`「方針が衝突する場合の優先順位」を適用し、通知がない場合と同様に本来の工程を継続する。
仕上げ、検証、公開、完了報告、振り返りなど、依頼の達成に必要な後続工程を維持する。

- 通知を受けた時点で、完了済みの成果、未完了の工程、途中で打ち切られた場合の再開位置、および再開情報を保存した絶対パスをユーザーへ報告する。強制終了後にはファイルを書き込めなくなるため、再開情報はこの時点で保存し、工程が進むごとに内容を更新する。
- `agents_server`において`api_error.type`が`usage_limit`となっている委譲先は、制限解除後に同一セッションで自動的に処理を再開する。バックグラウンドの`atk agents wait`や定期確認タスクはそのまま維持し、停止の要否は`agent-toolkit:delegation`の`references/waiting-stalls.md`「停滞の検知と巻き取り」に従って判定する。
- 猶予通知内に記載されている「実行中のサブエージェント」は、同一会話内で`Agent`ツールを用いて起動した委譲先を指す。独立したセッションとして動作する`agents_server`の解除待ちに対して、その停止案内を適用しない。
- 再開情報の記録先は状況に応じて選択する。計画書が存在する場合は`agent-toolkit:plan-mode`の計画ファイル内にある`## 進捗ログ`に記録する。既存の引き継ぎ記録が存在する場合はその記録ファイルに、いずれも存在しない場合は`agent-toolkit:managed-temp`で確定したセッションのmanaged-temp配下にあるMarkdownファイルに記録する。目的、承認の内容と出所、対象ワークツリーの絶対パス、成果物の絶対パスまたはコミットハッシュ、完了した工程と検証結果、および順序付きの未完了工程を、次に引き継ぐエージェントが理解できる形式で記載する。
- 中断後にユーザーからの再開指示、Function hooks moduleによる再開プロンプト、またはホストによる自動継続を受けたClaude Codeのメインエージェントは、保存された再開情報を読み込む。計画書を用いる場合は`agent-toolkit:plan-mode`の進捗ログ読み取り手順に従い、現在のファイル実態と記録された成果を突き合わせ、すでに得られている承認と成果に基づいて最初の未完了工程から作業を再開する。主成果物が確定した後に完了処理が残っている場合は、`agent-toolkit:completion-report`の適用条件および「工程」の再開条件を用い、元の適用内容と未完了工程を引き継ぐ。保存先や承認情報を復元できない場合は、不足している情報をユーザーへ確認する。

制限解除後の再開はFunction hooks moduleまたはホストが自動的に起動するため、猶予通知を受信した時点の報告において、ユーザーへ再開指示を求める必要はない。

猶予通知を受けた後も権限やツールの利用契約を遵守し、ホストによって強制終了された場合は中断として処理する。ユーザーから明示的な中止指示を受けた場合は、その指示に従う。
