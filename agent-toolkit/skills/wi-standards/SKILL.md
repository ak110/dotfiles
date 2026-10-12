---
name: wi-standards
user-invocable: false
description: >
  AWIまたはUWIの起草、読解、採否、状態判断および投入時、または対象リポジトリと
  private-notesを区別してGit操作するときに起動し、人間向けキュー項目の
  共通契約と通常の投入手順を提供する。
  手動投入専用のagent-toolkit:add-awi-by-userを明示起動した場合を除き、まず本スキルを起動する。
---

# AWIとUWIの共通規範

AWIはユーザーとエージェントが非同期に共有する未完了の作業要求、UWIはユーザーに残る判断と行動（再開に必須の判断に限らず、任意の対応の選択と操作結果の回答を含む）である。
UWIには、回答まで元の作業を再開・確定できない事前承認型と、最善と判断した対応を実施したうえで可否を問う事後承認型がある。
WI作成工程の入力、責務および出口は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。
自律モードのユーザーへの報告と回答はUWIへ記録し、その保存を伝達の完了とする（保存する条件と手段は`agent-toolkit:user-confirmation-and-report`「手段の選択」）。別項目の完了待ちと時間経過待ちはUWIにせず、依存と冷却時間で扱う。
WIの投入は、次回以降の`agent-toolkit:process-wi`が処理する要求をキューへ保存する操作であり、投入元セッションの処理対象には加えない。現在の依頼と承認の範囲で必要な是正はそのセッションで完遂し、後続にも独立した作業要求が残る場合だけ別途WIを投入する。
対象リポジトリはAWIが対象とする実装リポジトリ、private-notesはキューを保持するリポジトリである。WIはユーザーの手元の記録であり、外部の読み手が参照する成果物へこれらの識別子を書かない（`agent-toolkit:writing-standards`の`references/writing.md`「人間向け文章の共通規定」）。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 対象リポジトリとprivate-notesを扱う前 | `references/repository-scope.md` |
| 委譲先が`atk wi`でキュー項目の状態を変更する場合 | `references/subagent-queue-ops.md` |
| 通常AWIを起草・読解・採否判定する前 | `references/awi-body.md` |
| AWIまたはUWIを起草する場合（投入前に採否と実現方式を確定し、本文を読み直す時点） | `references/drafting-completion.md` |
| UWIを起草する場合 | `references/uwi-format.md` |
| WIを起草する時点と、WIの由来、承認、`source`の値、ユーザー専用の記入欄または逐語転記を判定する時点 | `references/origin-and-approval.md` |
| WIの状態、状態遷移、依存、冷却時間、保留と終端を扱う前。既存WIの更新・修復・採否の見直しが必要と分かった時点と、既存WIへの具体的な懸念を受けた時点（調査や認識合わせより先） | `references/states-and-dependencies.md` |
| 複数リポジトリへ投入する場合と、同じ成果の整合を要求する複数のAWIを登録する場合 | `references/cross-repository-submission.md` |
| 同じ対象リポジトリの複数項目を一括取得する場合 | `references/wi-show-bulk.md` |
| 通常AWIの投入、または投入済みAWIの内容の更新をWI投入担当へ委譲する前 | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` |

本文は`agent-toolkit:writing-standards`に従い、受け取ったエージェントが対象と期待結果を理解して完了へ進める粒度で書く。
AWIの粒度（要求の同一性で決める）: 既存・起草中AWIの目的、対象、完成条件の具体化や変更はそのAWIを更新する。別の目的を持ち、単独で採否・実装・完成判定が成立する要求は別AWIにする。同じファイル・節・文を編集することは合流の理由にせず、独立性を判定した後の書込競合として扱う。独立要求は両方の目的を保ち、先行成果が不可欠でない組は、どちらの順で実施してもその時点の成果物へ作用して完成できる本文にする。先行成果の必要性は`references/states-and-dependencies.md`、既存項目による被覆は`references/drafting-completion.md`「重複判定」で別に判定する。
反映内容と反映先の調査は投入前に終える。短縮は重複と判断を変えない記述の除去で行い、略語は本文か参照先で定義してから使う。文字数・行数の機械的な上限は設けない。

## 通常AWIの本文

本文形式、節の分担、完成条件と実現性は[references/awi-body.md](references/awi-body.md)が定める。必須H2の集合と順序は`atk wi add`が保存前に確かめる。
`## 反映内容と反映先`でWI処理の工程や運用を変える通常AWIを起草する場合は、起草前に`agent-toolkit:workflow-overview`を起動する。

### 起草完了の条件

AWIとUWIは採否と実現方式を投入前に一案へ確定し、投入前の読み直しを満たした本文だけを投入する。詳細は[references/drafting-completion.md](references/drafting-completion.md)が定める。

## 由来と承認

`source`の欠落だけを人間由来の初期値とし、値を持つ項目は要求単位に明示由来が無い限りエージェント由来とする。人間由来の要求単位を全部か一部不採用にする場合と、ユーザーが範囲語か開放列挙で示した集合を狭める場合は、ユーザーの明示承認を要する。AWIの`## ユーザーコメント`とUWIの`## 回答`はユーザーだけが書き込む。
詳細は`references/origin-and-approval.md`が定める。

## 状態と依存

WIの保存状態は`inbox`、`processing`、`hold`、`adopted`、`rejected`であり、`agent-toolkit:process-wi`は`inbox`と`processing`だけを処理する。既存WIの更新・修復・採否の見直しが必要と分かった時点と、既存WIへの具体的な懸念を受けた時点では、調査や認識合わせより先に対象を保留する。
処理中の項目の扱い: 別セッションが処理中の`processing`の項目は保留も本文の置換もせず、書き換えたい内容を新しい項目として投入する。
状態遷移、採否操作、保留と本文修正、終端、依存と冷却時間、事前承認型UWIの元項目の保留と「回答済みUWIの取り込み」は[references/states-and-dependencies.md](references/states-and-dependencies.md)、上流リポジトリへの投入は[references/cross-repository-submission.md](references/cross-repository-submission.md)が定める。

## 事後承認型UWI

事後承認の判定は`agent-toolkit:user-confirmation-and-report`の`references/approval-scope.md`に従う。事後承認型UWIは実施した対応の可否を問い、投入したエージェントは回答を待たずに元の作業を続ける。本文の書式は[references/uwi-format.md](references/uwi-format.md)が定める。
是正を求める回答に応じた是正の適否もエージェントが観測できない場合は、そのUWIを終端して新しい事後承認型UWIを投入する。

## 投入と取得

WIの投入前と投入済みWIへの着手前に、対象リポジトリの回答済みUWIを`atk wi list --target-repo=<対象リポジトリの絶対パス> --type=uwi --answered=yes --skip-pull --summary-only`で取得する。同じ実装領域の項目は`atk wi show`で回答を読み、新しい要求と両立するか確かめる。両立しない部分は投入・着手せず、原要求と回答を上位として本文を是正する。ユーザーだけが決められる値が残る場合だけ確認する（`agent-toolkit:user-confirmation-and-report`「確認要否の判定」）。
WIの追跡先と本文形式を同じ工程に求めるリポジトリでは、その規範の形式と時機に従って記載し、登録後に完了を確かめる。
依存を伴う要求は、`references/states-and-dependencies.md`の先行成果の必要性基準で確定した依存だけを後続本文と`depends_on`へ反映する。
投入済みの項目へ着手するエージェントは、`## 反映内容と反映先`・`## 完成条件`と`## 適用範囲`の対象集合の一致を`references/drafting-completion.md`「投入前の読み直し」と同じ基準で確かめ、不一致は実装へ進まず原要求と現物から復元して本文を訂正する。

1. エージェントが通常AWIを投入するとき、および投入済みAWIの本文を更新するときは、自ら起草せず、調査、起草、投入を`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`に従ってWI投入担当へ委譲する。作業文脈で占有されたエージェントが起草すると既存規定の適用を誤るためである。既存AWIの更新では対象の`ファイル名`を渡し、保留と解除は委譲元が行う。委譲先は確認を発行できないため、認識の違いで要件や結果が変わる未確定事項は回答を得てから渡す。
2. 本文はファイルへ保存し、`atk wi add --body-file <path>`か`atk wi edit --body-file <path>`で渡す（本文をコマンドライン引数で渡す形式は無い。手元で短い本文を書く場合は`$EDITOR`を使う）。同じ対象リポジトリへ同時に確定した複数件は、`--body-file`を繰り返して1回の`atk wi add`へまとめる（努力目標。commitとpushが1回で済む）。依存する複数件は先行項目から投入し、返った正式ファイル名を後続の`depends_on`と本文へ記入する。
3. 本文が確定した順に登録し、セッション終了時までためない。
4. 登録するAWIの対象ファイルが現在のセッションの変更対象と重なる場合は、別の作業ツリーでの並行着手と着手の遅れを比べて登録の時機を選ぶ（努力目標）。
5. エージェント自身の投入では「由来と承認」で確定した`source`を付け、`source`が未指定の人間由来入力は値を付けずに登録する。
6. `atk wi add`と`atk wi edit`は送信元と保存本文の一致確認、private-notesのcommitとremote反映まで行う。終了コード0なら保存本文の再取得は不要である。口語表現とダッシュの表記警告は行番号をもとに起草者が確かめれば完了扱いとし、その他の警告やエラーは修復する。未push commitの通知がある場合だけ`atk wi commit`で反映する。
