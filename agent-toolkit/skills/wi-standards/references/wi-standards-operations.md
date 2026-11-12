# AWIとUWIの共通規範：投入と取得

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 対象リポジトリとprivate-notesの操作を選ぶ前 | `repository-scope.md` |
| 外部更新の反映待ちを選ぶ前 | `../../delegation/references/waiting-and-monitoring-external.md` |
| 回答内容と要求が両立しない場合の確認要否を判定する前 | `../../user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` |
| 依存関係を確定する前 | `states-and-dependencies.md` |
| WI投入担当への委譲を選ぶ前 | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` |
| WIの起草主体と独立した検収を選ぶ前 | `drafting-workflow.md` |
| WIの由来を確定する前 | `origin-and-approval-origin.md` |

## 投入と取得

1. ユーザーから現在のセッションに対して、キュー項目の外部更新（UWIへの回答、AWIの投入や一括投入、ユーザーコメント、本文の編集、状態遷移など）が通知された場合は、`atk wi show <WIファイル名> --pull`を実行して個別の本文を強制同期したうえで取得する。
   対象項目を特定するための一覧取得でも、`atk wi list`に`--pull`を付与する。
   強制同期しても項目が見つからない場合や更新前の内容のままの場合は、送信側の保存処理が完了していない可能性を考慮する。
   再取得するエージェントは、`../../delegation/references/waiting-and-monitoring-external.md`「外部サービスの状態変化を待つ場合」に従って次回の確認時点を決める。
   その時点において、項目が特定済みであれば`atk wi show <WIファイル名> --pull`を、未特定であれば直前と同じ抽出条件の`atk wi list --pull`を再実行する。
   同期の成功と更新内容の到達は区別して扱い、private-notesに対する直接のGit操作は`repository-scope.md`の公開操作と例外条件に従う。
   なお、通常の軽量な参照、投入前に行う回答済みUWIとの整合確認、およびすでに同期済みの候補に対する定期的な参照には、この強制同期の条件を適用しない。

2. WIの投入前、および投入済みWIへの着手前には、対象リポジトリの回答済みUWIを`atk wi list --target-repo=<対象リポジトリの絶対パス> --type=uwi --answered=yes --skip-pull --summary-only`で取得する。
   同一の実装領域に関わる項目については`atk wi show`で回答内容を確認し、新しい要求と両立するかを確かめる。
   両立しない部分がある場合は投入や着手を控え、元の要求と回答内容を優先して本文を是正する。
   ユーザーだけが判断できる値が残る場合に限り、ユーザー確認する（`../../user-confirmation-and-report/references/user-confirmation-and-report-decisions.md`「確認要否の判定」）。
   WIの追跡先と本文形式が同一工程で指定されているリポジトリでは、その規範が定める形式とタイミングに従って記載し、登録後に完了を確認する。
   依存関係を伴う要求については、`states-and-dependencies.md`の先行成果の必要性基準に基づいて確定した依存関係のみを、後続の本文と`depends_on`へ反映する。
   投入済みの項目は、目的、変更内容、原因分析と固有の補足条件から完成状態を導き、旧形式の節も読んで対象を確定する。
   任意節や引用位置の記録を省いた本文も、その記載内容から着手できる。

3. キュー原文の変化に対する過剰な追加対策は実用上の対象外とし、原文のスナップショット保存、ハッシュ比較、差分比較、変化時の処理遮断やUWI化などは追加しない。
   現行のキュー取得機能を利用し、追加の恒常的な状態を持たない設計とする。
   検収用の一時的な原本保存や既存の取得機能の利用は、この追加対策には当たらない。

4. 作成主体と検収を決める。
   別の分析・実装中の主体が通常AWIを投入・更新する場合は、`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の付随型で独立した担当へ調査・起草を委譲し、親の意味検収後に同じ担当が保存する。
   WI作成専用の`add-awi-by-user`では件数によらずメイン自身が調査・起草・保存することを原則とし、専念型の委譲条件は同資料「起動方法」に従う。
   各作成主体は`drafting-workflow.md`の共通手順と「直接起草と独立した検収の選択」を適用する。
   更新は`ファイル名`、既存証拠と要件差分を使い、保留・解除は状態契約に従う。
   ユーザー固有の値は確認できるメインへ戻す。

5. 由来を保持するため、ユーザー指示による起票も含め、`origin-and-approval-origin.md`「由来と`source`」で確定した`source`を付与する。
   ユーザーの直接登録は`source`が無く、人間由来の初期値となる。

6. 本文が確定した順に、ファイルへ保存して`atk wi add --body-file <path>`または`atk wi edit --body-file <path>`で渡す。
   本文を直接渡すコマンドライン引数は無い。
   ユーザーの短い本文の直接入力には`$EDITOR`も使える。
   同じrepoで同時に確定した複数AWIは`--body-file`を反復して1回のaddにまとめる（努力目標。commitとpushを1回にする）。
   依存があれば先行項目から投入し、正式名を後続の`depends_on`と本文へ記す。
   変更対象が現在の作業と重なる場合は、別worktreeでの並行着手の利点と着手遅延を比べて投入時機を選ぶ（努力目標）。

7. `atk wi add`・`atk wi edit`による本文一致確認、private-notesのcommit・pushと終了コードを検収する。
   終了0なら本文の再取得は不要とする。
   意味・構造や保存操作の警告・エラーを確認し、表現だけの推敲と表記診断の確認は対象外とする。
   未プッシュ通知がある場合だけ`atk wi commit`で反映する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| ユーザー確認とユーザーへの報告の基準を適用する前 | `../../user-confirmation-and-report/SKILL.md` |
