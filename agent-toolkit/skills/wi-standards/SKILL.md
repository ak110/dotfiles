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

ユーザーがWIを読む場面は、UWIへの回答、AWIの内容の確認、処理後の結果の確認の3つである。本文の先頭には、その場面で知りたいことへ答える人向けの部分を置き、エージェント向けの詳細を続ける。AWIは問題・直し方・類似見直しの観点と範囲・再発防止策、UWIは問い・理由・答え方、処理後は何をどう直したかを先に示す。表現は`agent-toolkit:writing-standards`の`references/writing.md`「文章の共通規定」に従う。

AWIはユーザーとエージェントが非同期に共有する未完了の作業要求、UWIはユーザー対応を要する未完了事項と手順が定める完了報告を扱う項目である。
ユーザーの判断・資料・操作が次の作業に必要な場面などの登録要否は、`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。
UWIには、回答まで元の作業を再開・確定できない事前承認型と、最善と判断した対応を実施したうえで可否を問う事後承認型がある。
WI作成工程の入力、責務および出口は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。
自律モードのユーザーへの報告と回答はUWIへ記録し、その保存を伝達の完了とする（保存する条件と手段は`agent-toolkit:user-confirmation-and-report`「手段の選択」）。別項目の完了待ちと時間経過待ちはUWIにせず、依存と冷却時間で扱う。
WIの投入は、次回以降の`agent-toolkit:process-wi`が処理する要求をキューへ保存する操作であり、投入元セッションの処理対象には加えない。現在の依頼と承認の範囲で必要な是正はそのセッションで完遂し、後続にも独立した作業要求が残る場合だけ別途WIを投入する。
対象リポジトリはAWIが対象とする実装リポジトリ、private-notesはキューを保持するリポジトリである。WIはユーザーの手元の記録であり、外部の読み手が参照する成果物へこれらの識別子を書かない（`agent-toolkit:writing-standards`の`references/writing.md`「文章の共通規定」）。

キューの取得・保存・状態変更には対象リポジトリとprivate-notesへのアクセス、`atk`と必要な委譲手段を要する。これらが無い環境では、提示されたWIの読解と本文原稿の整理までを行い、未取得の既存項目と保存状態は未確認として残す。正式な投入・更新・終端はそれらの機能を使えるClaude CodeまたはCodexで行う。本文の説明は[文章の品質基準](references/quality-standards.md)に従う。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 対象リポジトリとprivate-notesを扱う前 | `references/repository-scope.md` |
| 委譲先が`atk wi`でキュー項目の状態を変更する場合 | `references/subagent-queue-ops.md` |
| 通常AWIを起草・読解・採否判定する前 | `references/awi-body.md` |
| AWIまたはUWIを起草する場合（投入前に採否と実現方式を確定し、本文を読み直す時点） | `references/drafting-completion.md` |
| WIの調査・起草・保存を担う前 | `references/drafting-workflow.md` |
| UWIを起草する場合 | `references/uwi-format.md` |
| WIを起草する時点と、WIの由来、承認、`source`の値、ユーザー専用の記入欄または逐語転記を判定する時点 | `references/origin-and-approval.md` |
| WIの状態、状態遷移、依存、冷却時間、保留と終端を扱う前。既存WIの更新・修復・採否の見直しが必要と分かった時点と、既存WIへの具体的な懸念を受けた時点（調査や認識合わせより先） | `references/states-and-dependencies.md` |
| 複数リポジトリへ投入する場合と、同じ成果の整合を要求する複数のAWIを登録する場合 | `references/cross-repository-submission.md` |
| 同じ対象リポジトリの複数項目を一括取得する場合 | `references/wi-show-bulk.md` |
| 通常AWIの投入、または投入済みAWIの内容の更新をWI投入担当へ委譲する前 | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` |

本文は`agent-toolkit:writing-standards`に従い、受け取ったエージェントが対象と期待結果を理解して完了へ進める粒度で書く。
AWIの粒度（要求の同一性で決める）: 既存・起草中AWIの目的、対象、完成条件の具体化や変更はそのAWIを更新する。観測した問題（不具合、未対応、品質の不足など）を起点とする要求では、根本原因（誤りを成立させた構造・判断規則）を共有する問題を同じ要求とする。症状が出た成果物、目的の字面や単独で完成判定できることだけでは分けない。既存・起草中AWIの根本原因が未確定の間に同じ症状か同じ誤りを別の対象で示す問題が加わったら、類似見直しの母集団の候補として起草中の担当へ要件更新で渡し、根本原因の違いが確定した場合だけ別AWIにする。根本原因を共有しても是正先の所有リポジトリが異なる対象は、`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`「起動前の前提」で投入先との組へ展開する。1件のAWIの中でのバグ単位は`agent-toolkit:bugfix`の`references/analysis-records.md`「原因分析の記録先」に従う。それ以外の独立した要求は、別の目的を持ち単独で採否・実装・完成判定が成立する場合に別AWIにする。同じファイル・節・文を編集することは合流の理由にせず、独立性を判定した後の書込競合として扱う。独立要求は両方の目的を保ち、先行成果が不可欠でない組は、どちらの順で実施してもその時点の成果物へ作用して完成できる本文にする。先行成果の必要性は`references/states-and-dependencies.md`、既存項目による被覆は`references/drafting-completion.md`「重複判定」で別に判定する。
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
是正を求める回答に応じた対応も、`agent-toolkit:user-confirmation-and-report`「手段の選択」で新しいUWIの登録要否を判定する。環境制約による未確認範囲は、可能な確認の結果とともに報告する。

## 投入と取得

ユーザーが現在のセッションへキュー項目の外部更新（UWIへの回答、AWIの投入・一括投入、ユーザーコメント、本文編集、状態遷移など）を告げた場合は、個別本文を`atk wi show <WIファイル名> --pull`で強制同期して取得する。項目を特定する一覧も`atk wi list`へ`--pull`を付ける。強制同期後も項目が見つからないか更新前の内容なら、保存側の送信が未完了の可能性を保つ。再取得するエージェントは、`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「外部サービスの状態変化を待つ場合」に従って次の確認時点を決める。その時点で、項目を特定済みなら`atk wi show <WIファイル名> --pull`、未特定なら直前と同じ一覧条件の`atk wi list --pull`を再実行する。同期成功と更新の到達は区別し、private-notesの直接Git操作の扱いは`references/repository-scope.md`の公開操作と例外条件に従う。通常の軽量参照、投入前の回答済みUWIとの整合確認と、既に同期した候補の定期参照には本条件を適用しない。

WIの投入前と投入済みWIへの着手前に、対象リポジトリの回答済みUWIを`atk wi list --target-repo=<対象リポジトリの絶対パス> --type=uwi --answered=yes --skip-pull --summary-only`で取得する。同じ実装領域の項目は`atk wi show`で回答を読み、新しい要求と両立するか確かめる。両立しない部分は投入・着手せず、原要求と回答を上位として本文を是正する。ユーザーだけが決められる値が残る場合だけ確認する（`agent-toolkit:user-confirmation-and-report`「確認要否の判定」）。
WIの追跡先と本文形式を同じ工程に求めるリポジトリでは、その規範の形式と時機に従って記載し、登録後に完了を確かめる。
依存を伴う要求は、`references/states-and-dependencies.md`の先行成果の必要性基準で確定した依存だけを後続本文と`depends_on`へ反映する。
投入済みの項目へ着手するエージェントは、`## 反映内容と反映先`・`## 完成条件`と`## 適用範囲`の対象集合の一致を`references/drafting-completion.md`「投入前の読み直し」と同じ基準で確かめ、不一致は実装へ進まず原要求と現物から復元して本文を訂正する。

1. 他作業の分析・実装の文脈を持つ主体が通常AWIを投入・更新するときは、`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の付随型として調査・起草を独立担当へ委譲し、親の意味の検収を経て同じ担当が保存する。専念型（WI作成専用の`add-awi-by-user`と、`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`「起動方法」が定める場面）は、作成主体が`references/drafting-workflow.md`の共通手順と投入前レビューを経て保存まで完結する。`add-awi-by-user`は同スキルの独立要求数で作成主体を決める。既存AWIの更新では対象の`ファイル名`と取得済み証拠・要件差分を使い、状態の保留と解除は状態契約に従う。ユーザーだけが決める値は確認できるメインへ戻す。
2. 本文はファイルへ保存し、`atk wi add --body-file <path>`か`atk wi edit --body-file <path>`で渡す（本文をコマンドライン引数で渡す形式は無い。手元で短い本文を書く場合は`$EDITOR`を使う）。同じ対象リポジトリへ同時に確定した複数件は、`--body-file`を繰り返して1回の`atk wi add`へまとめる（努力目標。commitとpushが1回で済む）。依存する複数件は先行項目から投入し、返った正式ファイル名を後続の`depends_on`と本文へ記入する。
3. 本文が確定した順に登録し、セッション終了時までためない。
4. 登録するAWIの対象ファイルが現在のセッションの変更対象と重なる場合は、別の作業ツリーでの並行着手と着手の遅れを比べて登録の時機を選ぶ（努力目標）。
5. エージェント自身の投入では「由来と承認」で確定した`source`を付け、`source`が未指定の人間由来入力は値を付けずに登録する。
6. `atk wi add`と`atk wi edit`は送信元と保存本文の一致確認、private-notesのcommitとremote反映まで行う。終了コード0なら保存本文の再取得は不要である。口語表現とダッシュの表記警告は行番号をもとに起草者が確かめれば完了扱いとし、その他の警告やエラーは修復する。未push commitの通知がある場合だけ`atk wi commit`で反映する。
