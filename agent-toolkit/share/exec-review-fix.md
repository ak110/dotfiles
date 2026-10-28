# レビュー修正とCI修正の手順

本書は、`exec.subagent.md`の`担当種別`が`レビュー修正担当`または`CI修正担当`のエージェントと、レビュー修正の履歴統合をするエージェントが、修正と履歴統合を進める手順を定める。入力、操作区分、返却形式は`exec.subagent.md`に従う。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 入力、操作区分、phaseの対象、返却値を確かめる時 | `exec.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec.subagent.md` |
| `CI修正担当`として原因を調べる前 | `../skills/bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/exec-review-recording.md#「失効の根拠」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「失効の根拠」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/base-contract.md#「入力の適格性と追送」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「入力の適格性と追送」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「初動と拡張原因分析の判定」「直接的原因と事象時点の確定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「初動と拡張原因分析の判定」「直接的原因と事象時点の確定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/commit/references/push-and-ci.md#「pushと監視」` | 判断 | `section:agent-toolkit:*/skills/commit/references/push-and-ci.md#「pushと監視」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md#「類似見直し」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「類似見直し」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「根拠の書き方」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/reviewee.md#「安全な修正」「公開可能性の検証」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「安全な修正」「公開可能性の検証」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/references/reviewee.md#「修正要否の立証」「安全な修正」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「修正要否の立証」「安全な修正」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/commit/references/history-rewrite.md#「修正方法の選択」` | 判断 | `section:agent-toolkit:*/skills/commit/references/history-rewrite.md#「修正方法の選択」` |

## 担当種別ごとの修正

レビュー修正やCI修正においても、渡された是正項目とファイル内の他箇所の整合性については、`agent-toolkit:delegation`の`references/base-contract.md`「入力の適格性と追送」に従う。
会話圧縮の後にレビュー修正を再開する場合は、担当種別の切り替えの有無にかかわらず、`agent-toolkit:review-standards`の`references/reviewee.md`「安全な修正」「公開可能性の検証」を再読する。

- `レビュー修正担当`は、採用した指摘のみを修正し、レビュー指摘管理表が要求する証拠を保存する。採否の決定や横展開は、`agent-toolkit:review-standards`の`references/reviewee.md`「修正要否の立証」および「安全な修正」に従い、レビュー担当者の修正方針をそのまま要件とみなさずに自身で妥当性を確定する。不採用とした指摘については理由を管理表に記録し、成果物は修正前の状態を維持する。計画やWIで確定している内容を覆す修正は行わず、対立が生じる箇所を「続行できない理由」として報告する。
- ユーザビリティレビューの指摘を修正した担当者は、指定された操作手順、画面状態、表示幅に基づき、実ブラウザで新しいHEADを描画して結果を確認し、試行した条件と結果を再提出の記録に記載する。自動化が可能な検証条件は既存のブラウザテストに追加して保持する（努力目標。同一条件のテストの無駄な重複を避ける）。
- `CI修正担当`は、CI記録に含まれる7項目を実装契約とし、原因となった失敗のみを修正する。原因調査の着手前に`agent-toolkit:bugfix`を起動し、`agent-basics:bugfix/references/root-cause-analysis.md`「初動と拡張原因分析の判定」「直接的原因と事象時点の確定」に定める共通判定を、CI記録の原因分析結果に適用する。直接的原因を特定できない場合は是正コミットとして返さず、実施した比較検証、残された可能性、および作成した診断用コードの変更内容を、推定である旨を明記して報告する。診断コードを追加する場合は、CI記録が紐付けている競合仮説、観測事項、採取対象をあらかじめ確認し、識別に寄与しない網羅的な列挙や走査を避け、採取対象を絞り込んで出力量を抑える。修正手順は`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」におけるCI失敗の分類に従う。原因コミットへ修正を取り込んだ場合は、書き換え前後のOIDの対応関係と`git range-diff`の実行結果を返す。原因となった失敗と根本原因を同じくする箇所のうちCI記録で認可されていない対象については、`agent-basics:bugfix`の`references/root-cause-remediation.md`「類似見直し」の対象群として、修正対象と修正の方向性を委譲元へ報告する。CIログの有無、再現性、外部の一時的な障害、再実行の認可状況や回数によってこの報告を省略することはない。リモートへのpushは行わない。

## レビュー修正の履歴統合

修正の適用方法（fixupの作成、元コミットへの対応付け、autosquashの実行単位）は、`agent-toolkit:commit`の`references/history-rewrite.md`「修正方法の選択」に従い、autosquashはレビューラウンドごとに実行する。fixupの対象が直前の1コミットのみである場合は、autosquashの代わりに`git commit --amend`を使用してもよい。

- 履歴を書き換える前に、対象worktreeがクリーンであること、書き換え対象が未pushであること、および操作直前に確認した書き換え対象のOID群が自身のレーンのコミットのみであることを確認し、別のコマンド呼び出しで同書「履歴確認の起動形」の`git log`を実行する。
- autosquashは同書「fixupの実行上の制約」に定める起動形式で実行し、競合や失敗が発生した場合は同書「失敗時の扱い」に従う。同一指摘に対する履歴統合の再試行は、委譲元の判断を得てから実施する。
- 書き換え前後のOID、tree、親OID、コミット件名を保持し、`rewrite_guard`の要求を満たしていることを確認する。続いて同書「操作前後の確認」の起動形式を実行し、終了コード0と検収済み対応表の保存を確認する（autosquashの場合は`atk run-script history-compare -- --operation autosquash`、amendの場合は`--operation amend`）。
- レビュー指摘管理表に、指摘内容、採否、修正コミット、検証結果、書き換え前後の7文字以上の一意な短縮OIDの対応関係、一致確認結果、および対応表の絶対パスを記録する。この完全なOID対応表を`atk run-script plan-rewrite --`へ同書の起動形式で渡し、AWI群の紐付けを現在の実装コミットへ継承する。完成条件証拠については、委譲元から`rewrite_map`を受け取った実行レビュー担当者が新しいHEADで再判定する。
- 履歴統合後の変更範囲の検証は`diagnostics.md`に従い、記録済みの基準版と累積差分から判定して、`atk run-script plan-progress --`で進捗ログに追記する。
- 取得したバージョンがレビュー対象の祖先から外れている検証結果については、結果に影響を及ぼす差異がある検証や取得バージョンを特定できない検証のみを新しいHEADで再取得する。それ以外の検証は、`exec-review-recording.md`「根拠の書き方」に従って取得時のHEAD、観測結果の所在、および適用理由を`検証結果`に記載する。
- 会話履歴が失われた状態で再開する場合は、レビュー指摘管理表の完了内容を現在のGit実体と突き合わせ、保存済みの書き換え前後OID対応、公開済み判定、コミット件名の一意性から確実に特定できる場合に限り処理を進める。旧OIDは保存済みの記録から取得し、指定のないreflogを出典として用いることは避ける。特定できない場合は、対象OIDと不足している情報を「続行できない理由」として返す。

各phaseの対象と返却値は`exec.subagent.md`「レビュー修正の履歴統合」に従う。
