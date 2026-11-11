# 実行レビュー担当の起動とレビュー修正：返却に含まれる既存不良の受領

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 差分外の不良を受領して対処時機を決める前 | `../skills/delegation/references/receiving-followup.md` |
| 形式判別前 | `../skills/wi-standards/references/awi-body.md` |
| 検証結果を記録する前 | `verification-evidence-records.md` |
| 手動観測を保存する前 | `verification-evidence-observation.md` |
| 最終の検証と有限の手動観測の時機を判断する前 | `../skills/commit/references/commit-verification.md` |
| 保存済み成功と失敗限定の結果を合成する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` |
| 初回起動前 | `../skills/delegation/references/process-model-settings.md` |
| process-wi中に差分外の既存不良や正式改善AWIを受領した時 | `agent-toolkit:process-wi/references/immediate-fix.md` |
| 返却の`達成`行の根拠を受領確認する前 | `exec-review-recording-grounds.md` |
| 基準の参照時 | `add-wi.parent.md` |
| 基準の参照時 | `agent-basics:bugfix/SKILL.md` |
| 基準の参照時 | `../skills/user-confirmation-and-report/SKILL.md` |
| 基準の参照時 | `add-wi-parent-acceptance.md` |
| 本文が再レビュー省略の条件を確認する時 | `agent-basics:review-standards/references/reviewee-verification.md` |
| 完成条件証拠の検収時に、計画や委譲プロンプトから複数のWI名を取得する場合 | `agent-toolkit:wi-standards`の`references/wi-show-bulk.md` |
| `review_contract`の要否を判断し、生成・再生成する前 | `exec-review-contract-generation.md` |
| レビュー工程の判定・記録を始める前 | `review-table-operations.md` |
| レビュー工程の判定・記録を始める前 | `exec-review-criteria.md` |
| レビュー工程の判定・記録を始める前 | `exec-review-responses.md` |
| レビュー工程の判定・記録を始める前 | `exec-review-recording.md` |

## 返却に含まれる既存不良の受領

**7.** 差分外の不良を処置へ対応付け、レビュー収束と並行して追跡する。

各ラウンド中に届いた通知、および返却と走査記録を受け取った時点で、今回の変更差分の対象外としてレビュー指摘管理表から外した候補と、通知・返却の`気付いた改善点:`行との対応を確かめる。
レーン担当経由の報告も同じく受領確認し、処置はメインエージェントが決める。

受領側は差分外の既存不良の`気付いた改善点:`行を要求別に保持し、`../skills/delegation/references/receiving-followup.md`「受領後の扱い」で対処時機を判定する。
正式対応は`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の専念型へまとめて渡す。
元作業・実行レビューから独立したWI投入担当が、似た対象から順に調べて知見を再利用する。
担当追加は同書の総費用比較で有利な場合に限る。
メインは`agent-basics:bugfix`の起動や走査記録からの原因・対策の読み取りをせず、原因分析からAWI保存まで投入担当に任せる。
調査・原因分析・原稿検収をレビュー受領側の文脈から分け、総費用を抑えるためである。

- 要求ファイルには、`気付いた改善点:`行の全文と走査記録の絶対パスを要求の趣旨と`確定済みの観測`に、原因・類似見直し・対策の検討項目を`投入前に確定すべき事項`に書く。
  投入先は該当行が示す対象ファイルを所有するリポジトリとし、`source`属性は受領元エージェントのスキルに基づき`../skills/wi-standards/references/wi-standards-origin.md`「由来と承認」で決める
- 起動した担当の`session_id`と対象の不具合を、該当レビューの引き継ぎ記録へ引き渡し済みとして対応付ける。
  再レビューや引き継ぎ再レビューで同じ不具合が再び報告されたら、この記録で引き渡し済みと判定し、担当を重ねて起動しない
- 投入担当の完了を待たずに、レビューの収束、統合、公開を並行して進める。
  投入担当の終了はバックグラウンド完了通知で確かめ、最終の作業完了報告の前までに全担当の終了を確かめる。
  この終了確認は統合や公開を始める前提条件から除く
- 保存済みAWIの正式名を受領したら、process-wi中は読込表の`immediate-fix.md`「即時対応」で今回追加を判定し、選定と担当への受渡しを確認する。
  正式名は`../skills/report-completion/references/report-formats-completion.md`「作業完了報告のフォーマット」の`### 投入したWI`でも報告する
- 担当から続行できない理由が返ったら、ユーザーの判断が要る事項を`agent-toolkit:user-confirmation-and-report`に従って確認する（自律モードではUWIへ退避する）。
  投入先リポジトリ外のファイルが対象として返った場合は、`add-wi-parent-acceptance.md`「続行できない理由の扱い」に従って投入先と要求の組を確定し直す

通常の次回送りは`agent-toolkit/share/rules/common/01-agent.md`「完遂と先送り」の一般原則の例外とし、事象・根本原因・対応案を投入担当がAWIへ保存する。
process-wi中は読込表の`immediate-fix.md`「即時対応」で正式改善の今回追加を判定し、最小修正後の正式対応は次回へ渡す。
残工程や次のセッションを停止・遮断する不具合は、観測根拠から「進行を妨げる欠陥」と判定し、詳細な原因分析に先行して直接修正する。
修正は`agent-basics:bugfix`と`01-agent.md`「完遂と先送り」、process-wi中の最小修正は前述の「即時対応」に従う。
正式対応では`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」で、品質影響・レビュー対象との根本原因の同一性・処置完了を判定する。
同スキルの`references/root-cause-remediation.md`「類似見直し」、`agent-basics:bugfix/references/root-cause-prevention-verification.md`「類似見直しと再発防止の完了」も適用する。
認可は`agent-toolkit:user-confirmation-and-report`に従う。
レーン担当が自レーンの検証を妨げるとして報告した既存不良は、実行レビューの返却と区別し、`process-wi`の処理中は同スキルの「即時対応」として扱う。

対象の不具合ごとに、引き渡し済みの担当、是正結果、根拠付きの非該当判定、判断待ちの引き渡し先のいずれかを既存の引き継ぎ記録へ対応付けてから、レビュー収束後の工程へ進む。
不採用の記録、報告済みであること、指摘管理表の未解決件数が0件であることだけでは処置完了としない。
差分外の不具合の対応状況はレビューの未解決件数と切り離して管理し、再レビューで新たに出た事項や未処置の事項も同じ記録で追跡する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 再発防止の選定と完了：類似見直しと再発防止の完了の基準を適用する前 | `agent-basics:bugfix/references/root-cause-prevention-verification.md` |
| 類似見直しの範囲と探索の基準を適用する前 | `agent-basics:bugfix/references/root-cause-remediation.md` |
| 根本原因と重大度：QとC・Dの区別の基準を適用する前 | `agent-basics:bugfix/references/root-cause-severity-impact.md` |
| AWIとUWIの共通規範の基準を適用する前 | `../skills/wi-standards/SKILL.md` |
