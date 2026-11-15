# レーン統合の失敗

本書は、`../../../share/lane-integration-subagent-operation.md`「マージありの統合」において、mergeが競合によって中断した場合、およびmerge後の変更範囲の検証が失敗した場合の対処手順を定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 統合後の是正で変更範囲の検証の対象を選び直す前 | `agent-basics:check-execution/references/verification-scope-selection.md` | | |
| レビュー指摘管理表へ担当が行を足す前 | `../../../share/review-table-operations.md` | | |
| 統合指示を再受領して統合を再開する時 | `../../../share/lane-integration-subagent-operation.md` | 判断 | `delegation-result`、`read:agent-toolkit:*/share/lane-integration-subagent-operation.md` |
| 競合解消のcommitとWIの対応を記録する時 | `../../commit/references/commit-wi-records.md` | | |
| 競合解消後のレビューと再開を判断する時 | `../../../share/exec-review-parent-correction.md` | | |
| 統合後の検証失敗の是正に着手する前 | `agent-basics:bugfix/SKILL.md` | | |

## mergeの競合

1. mergeの競合が返されたら、専用worktreeの両親OID、競合パス、両側の要求と解消方針を確認する。
   所有する要求の範囲で解消できる場合は同じレーン担当が続け、追加の判断や認可が必要な単位だけを委譲元へ返す。
   対象worktreeとmerge進行中の状態、必要な判断を報告し、未保存の成果は保つ。

2. 両側の要求を維持して競合を解消し、解消したパスだけをステージングして通常のmerge commitを作成する。
   既存の実装OIDはそのまま保持する。
   競合解消で生じた変更と影響するAWIを、`../../commit/references/commit-wi-records.md`「WI実装commitの対応」に従ってmerge commitへ対応付ける。
   競合箇所、解消方針、変更内容、影響範囲を`atk review-table add`でレビュー指摘管理表に1行追加し、同じ行に対して`atk review-table respond`で解消内容を記録する。
   追加する行の書き出しは`../../../share/review-table-operations.md`「レビュー指摘管理表の共通操作」に従う。
   影響する利用側を検証し、解消差分を独立実行レビューする。
   既存の実行レビュー担当から現在HEADの判定を受理する。

3. クリーンな現在HEADを`--reviewed-head`へ渡し、受理済みレビューとcommit対応を反映したmanifestで`atk lane integrate`へ`--restart-review`を付けて再開する。
   統合先がさらに前進した場合も同書「マージありの統合」に従って追加mergeし、その影響だけを検収する。
   旧新OID対応表の生成と、通常のmergeと無関係な履歴書換えの受理は対象外とする。

## 統合後の変更範囲の検証の失敗

1. レビュー済みHEADでは再現せず、統合先との組み合わせによってのみ発生した失敗: 失敗した検証が確認対象とする契約の送信側、受信側、実装、テストを整理し、専用worktreeに是正コミットを記録する。
   是正で変えた定義・文言・構成・実行前提の変更前の値を固定文字列で探し、それを消費する実装・検査・試験を変更範囲の検証の対象へ戻す（`agent-basics:check-execution/references/verification-scope-selection.md`「範囲の決め方」の再選定）。
   是正前のHEADで得た成功は是正の検証に充てない。
   変更範囲の検証を是正後のHEADで再実行して、成功することおよび処理を妨げる警告が存在しないことを確認した上で、コミットと検証の証拠（警告の判定根拠を含む）を「続行できない理由」としてメインエージェントへ返す。
   メインエージェントから再指示を受けた後は、同書「マージありの統合」における「統合後にのみ成立する検証の有無を確認する手順」へ進む。

2. 是正対象の失敗がレーンの変更内容と同一の根本原因を持つ場合: 是正の範囲および完了判定には、`agent-basics:bugfix`の`references/root-cause-remediation.md`「類似見直し」を適用する。
     `agent-basics:bugfix/references/root-cause-prevention-verification.md`「類似見直しと再発防止の完了」も併用する。
   認可範囲外の対象が含まれる場合は次項の手順に従って報告する。

3. レビュー済みHEADでも再現する失敗、および認可範囲外の変更が必要な失敗: 実行したコマンド、出力結果、作業を続行できない理由を`続行できない理由:`に明記して返す。
