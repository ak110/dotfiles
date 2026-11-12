# レーン統合の失敗

本書は、`../../../share/lane-integration-subagent-operation.md`「マージありの統合」において、rebaseが競合によって中断した場合、およびrebase後の変更範囲の検証が失敗した場合の対処手順を定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 統合後の是正で変更範囲の検証の対象を選び直す前 | `agent-basics:check-execution/references/verification-scope-selection.md` |
| レビュー指摘管理表へ担当が行を足す前 | `../../../share/review-table-operations.md` |
| 統合指示を再受領して統合を再開する時 | `../../../share/lane-integration-subagent-operation.md` |
| rebase競合の解消後に履歴を確認する時 | `../../commit/references/history-rewrite-verification.md` |
| 解消後の対応表を検収して引き継ぐ時 | `../../commit/references/history-rewrite-wi-records.md` |
| レビュー修正の履歴統合の返却値を返す時 | `../../../share/exec-subagent-history.md` |
| 統合後の検証失敗の是正に着手する前 | `agent-basics:bugfix/SKILL.md` |

## rebaseの競合

1. 停止時の対応: `git -c submodule.recurse=false rebase --abort`や`--continue`は実行せず、rebaseが進行中の状態を保ったまま、続行できない理由を委譲元へ返す。
   `続行できない理由:`には、競合が発生したファイルのリポジトリ相対パス、専用worktreeの絶対パス、および該当worktreeでrebaseが進行中であることを記載する。

2. 解消指示の伝達では開始前の旧系列と統合先のbase・headの各OIDを引き継ぐ。
   メインから解消指示を受領した場合: 競合を解消し、解消したパスのみをステージングする。
   `../../commit/references/history-rewrite-verification.md`「履歴確認の起動形」に記載された`git log`を単独で実行する。
   続いて`git -c submodule.recurse=false rebase --continue`を実行してrebaseを完了させる。
   競合箇所、解消方針、変更内容、影響範囲を`atk review-table add`でレビュー指摘管理表に1行追加し、同じ行に対して`atk review-table respond`で解消内容を記録する。
   追加する行の書き出しは`../../../share/review-table-operations.md`「レビュー指摘管理表の共通操作」に従う。
   その上で、`../../../share/exec-subagent-history.md`「レビュー修正の履歴統合」に定められた返却値を返す。

3. 統合指示を再受領したら、`../../../share/lane-integration-subagent-operation.md`「マージありの統合」の冒頭（専用worktreeのクリーン確認）から再開する。
   rebase完了後、まず`--resolved-conflict`と`--exclude-path`を付けずに同じ入力の`atk lane integrate`を再実行する。
   history-compareが「変更内容が対応しない旧commit」として示したOIDだけを`--resolved-conflict <旧OID>`で反復指定する。
   指定するのは、旧commitの変更を新しい親へ3-way適用した結果が新commitと一致しないものとし、周辺の文脈行だけの変化は含めない。
   旧commitの変更パスの一部が解消後に統合先と同一になるなど、意図して外したパスは`--exclude-path <旧OID> <リポジトリ相対パス>`へ反復指定する。
   同じ旧commitへの`--resolved-conflict`との併用、旧commitが変更していないパス、全変更パスの除外は拒否される。
   CLIは指定をhistory-compareへ渡し、検査に成功した集合だけを所有登録へ保存する。
   拒否された指定は保存されないため、指定を外すか直して同じ操作から再開する。
   両指定とも、省略した後続の再開では最後に成功した保存値を使う。
   競合解消で統合先の新しいファイルへ追随した場合など、旧コミットに無い変更パスが新コミットに増えた対応は、所有登録の`integration.results`に保存された`history-compare`の出力に列挙されるため、その追随が解消方針の範囲内かを対応表とともに検収する。
   対応表の検収と引き継ぎは、`../../commit/references/history-rewrite-wi-records.md`「WI実装commitの対応の継承」に従う。

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
