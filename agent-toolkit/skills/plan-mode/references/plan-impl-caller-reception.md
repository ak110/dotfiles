# 計画実装担当の起動と受領

呼び出し元は1計画1レーンの専用worktreeで`plan-impl-executor`を起動し、レーンの完了実体を検収する。
実装担当の内部手順は`implementation-task.md`、レビューの反復は`review-loop-coordination.md`を正本とし、この文書へ複製しない。

## 起動

同じ計画ファイルへ書き込む計画担当が終端したことを、保持した実行識別子の直接照会で確認する。
計画ファイル（メイン・詳細）、実装単位、先行依存、統合順、検証区分及び完了条件を全文読み、計画の対象を1つのレーンへ対応付ける。
1つの計画ファイルを複数レーンへ分割せず、複数の計画ファイルを同じレーンへまとめない。

各レーンについて、呼び出し元が専用managed-tempと専用git worktreeを作成する。
現在worktreeの借用、統合branch及び統合worktreeを使わない。
変更ファイルが重なる別レーンも独立して起動し、競合は各レーンのrebase工程で解消する。

作成直後に次を計画メインの`## 進捗ログ（実行時）`へ記録する。

- レーンの用途と対象計画
- レーン専用worktreeとmanaged-tempの絶対パス
- 作成時のHEAD完全OID
- 作成主体と所有主体
- 回収対象であること
- 実装レビュー用managed-tempと`review.tsv`の絶対パス

`plan-impl-executor`へ次を渡す。

- 計画ファイル（メイン・詳細）、プロジェクト規範、作成規範スキル及びタスク文書の絶対パス
- レーン専用worktree、レーンmanaged-temp及び実装レビュー用managed-tempの絶対パス
- ベースworktree、ベースbranch及び起動時のベースtip完全OID
- pickerが確定した順序を保持したフィードバックファイル名一覧
- フィードバック固有の処理順、公開、確認又は検証指示
- 複製元、対象外worktree、commit可、ffマージ可、所有資源の回収可、push不可という権限

計画が対象リポジトリ外への操作を列挙する場合は、列挙された対象だけを認可範囲として渡す。
agent定義とタスク文書が持つ手順、書式及び完了条件を起動文へ複製しない。

## checkpointの受領

`status: checkpoint`を受領した場合は、`checkpoint.type`別に次を検収する。

- `review_round`: ラウンド番号、`stage`、指摘件数、被覆結果、実害の概要、修正内容の要約、残る証拠不足及び次ラウンドの要否、修正前後HEADの完全OID
- `merge_request`: レーンHEAD完全OID、レーン内検証結果及びrebase要否

採用指摘の修正前に`stage: before_fix`を受領した場合は、`pre_rewrite_head`がレーンworktreeの現行HEADと一致することを実測し、`post_rewrite_head: なし`とともに`## 進捗ログ（実行時）`へ保存する。保存後に同じexecutorへ再開を指示し、保存前に修正担当を起動させない。
修正・履歴検収後の`stage: after_fix`では、`before_fix`で保存した`pre_rewrite_head`と返却値が完全一致し、`post_rewrite_head`と現行レーンHEADが完全一致することを確認する。保持済みの全実装単位の変更前後OID、件名、順序、親子関係及び差分帰属へ対応付ける。`git fetch --all --prune`後に変更前OIDごとの`git for-each-ref --contains=<変更前OID> refs/remotes/`を実行してremote ref包含が0件であることを検収する。`git rev-list --first-parent --merges <最古対象OID>^..<pre_rewrite_head>`の出力が0件であることを検収する。`git log --first-parent --format='%H%x09%s' <最古対象OID>^..<pre_rewrite_head>`で対象commit件名が各1件であることを検収する。全検査の終了コード0と合格結果を`## 進捗ログ（実行時）`へ保存した後だけ比較基準を`post_rewrite_head`へ更新する。remote ref包含、merge commit又は件名重複を検出した場合は再開せず、対象OID・ref・merge commit又は重複件名の実測値を付けて`needs_escalation`へ返す。
指摘なしの`stage: no_fix`では、`pre_rewrite_head`と`post_rewrite_head`が現行HEADと完全一致することだけを検収し、一致時は比較基準を変更せず再開する。不一致時は両OIDの実測値を付けて`needs_escalation`へ返す。`no_fix`では公開済み判定、merge commit不在及び件名一意性の検査を起動しない。

第3ラウンド以降の`review_round`では、メインが方向性、再設計及び継続可否へ介入する。
レビュー表の行数を停滞判定又は是正送付の条件にせず、一般的な待機と観測の契約を使う。

`merge_request`は1レーンずつ許可する。
許可時にベースworktreeとベースbranchの現在tip完全OIDを読み取り専用で再照合し、他レーンへ同時にマージ許可を与えない。
再開指示へ最新のベースtip完全OIDを渡し、同じexecutorを継続する。

認可範囲外、前提差異、ユーザー判断のいずれかを`needs_escalation`で受領した場合は、回答又は是正内容を確定して同じexecutorへ返す。
同じtaskと実効`engine`・`model`・`effort`が成立し、識別子が有効なら同じ実装担当threadを再開する。
route変更、識別子消失、前提無効時のいずれかだけ、既存担当の終端を確認した後に一般的な継続契約で新しく起動する。

## 完了の検収

`completed`を受領したら、報告より先に次を実測する。

1. 計画の各実装単位に対応するcommit、変更ファイル、件名、順序及び差分帰属
2. レーン内検証と計画の完了条件
3. `implementation-review`の対象HEAD、被覆結果、指摘と対応結果、`review.tsv`の実在及び`track`帰属
4. 最新ベースへのrebaseと、競合変更がある場合の同じ実装担当による解消及び再レビュー
5. ベースbranchへのffマージと、成功後のベースHEAD完全OID
6. 固有の延期指示がないフィードバックの1件ずつの`adopt`と保存結果
7. レーンが所有するbranch、worktree、実装レビュー用managed-temp及びレーンmanaged-tempの回収
8. 固有の延期指示がある場合のベースHEAD完全OID、未完了の終端順序及び実装資源の回収

レビュー修正の履歴書換えは`implementation-task.md`と`agent-toolkit:commit`の契約へ照合する。
phaseごとの`履歴書換え防止`、変更前後の完全OID、commit数、順序、親子関係、差分帰属、検証結果とclean状態を確認する。最終`merge_request`を再開する前に、最後に合格した`post_rewrite_head`と現行レーンHEADの一致、全実装単位のOID、件名、順序、親子関係と差分帰属を保存する。`after_fix`と同じremote ref包含0件、merge commit 0件と対象commit件名各1件の検査を再確認する。`atk review-table validate <review.tsvの絶対パス>`の終了コード0と警告なしも`## 進捗ログ（実行時）`へ記録する。禁止条件を検出した場合は、対象OID・ref・merge commit又は重複件名の実測値を付けてマージを許可せず`needs_escalation`へ返す。証拠不足の場合は同じexecutorへ不足範囲を返す。

`completed`報告の`履歴書換え防止`が必須項目を欠く場合だけ、欠落時の代替検収を行う。完全な報告では現行の検収をそのまま行う。代替検収では、回収前の進捗ログに保存した変更前後OID対応、公開済み判定、履歴書換え範囲のmerge commit不在、対象commit件名の一意性とレビュー表の構造を保存済み証拠として扱う。ffマージ後のベースbranchにある件名、順序、親子関係と差分帰属へ照合する。回収済みレーンの無指定reflogから旧OIDを復元しない。
Git実体から確定できないphaseごとの判定順序と、履歴書換え途中の担当引継ぎ有無だけを同じexecutorへ照会する。過去のGitコマンド終了コードとエラー要約は再送要求しない。応答を得られない場合は、当該項目を`不明`として記録する。保存済み証拠で代替した事実と、証明できない時間的・手続的範囲を`## 進捗ログ（実行時）`へ記録し、呼び出し元が残る読み取り専用の検収を巻き取る。実装担当の終端と書込所有権の解放を確認するまで巻き取らず、別主体も起動しない。この代替検収は報告欠落時だけに適用し、実装担当の`履歴書換え防止`必須出力、履歴書換え開始後の単一担当・公開済み判定と既存checkpoint種別を変更しない。

呼び出し元はcommit受領、各レビューラウンド、マージ及びレーン完了を`## 進捗ログ（実行時）`へ記録する。
報告と実体が異なる場合は実体を優先し、実作業又は証拠が不足する場合は同じexecutorへ返す。

## pushとCI

レーンはpushとCIを実行しない。
全レーン完了後のpush、CI、CI修正、固有の終端工程、延期した`adopt`及びセッション終了は`agent-toolkit:process-feedbacks`の③を正本とする。
