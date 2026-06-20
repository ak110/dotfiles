---
name: process-feedbacks
description: >
  ~/private-notes/feedback/inbox/配下のフィードバックを順に処理し、
  採用は対象リポジトリへ反映してファイルを削除、不採用は rejected/ へ移動する。
# 連携: target_repoごとにグループ化し、各グループの全件をまとめて agent-toolkit:apply-feedback へ委譲する。
# フラグファイル ~/.config/agent-toolkit/feedback-inbox.enabled が存在する環境でのみ動作する。
---

# フィードバック消化

## 起動方針

`~/.config/agent-toolkit/feedback-inbox.enabled`が存在しない場合は、
フィードバック蓄積機能が無効である旨を1文示して終了する。

`~/private-notes`が存在しない場合は、手動で`~/private-notes`をクローンしてから
再度実行する旨を案内して終了する。

## ステップ1: 事前準備の確認

`dotfiles-fb`の状態変更系サブコマンド（`add`・`adopt`・`reject`・`rm`・`edit`）が内部で`git pull --ff-only`を実行するため、手動での`git pull`実行は不要とする。
`list`サブコマンドはローカル状態のみを参照するためpullを実行しない。

## ステップ2: 件数確認とグループ化

`dotfiles-fb list`を実行し、標準出力を解釈して件数をユーザーに提示する。
0件の場合は「処理対象なし」を1文示して終了する。

`dotfiles-fb list`の出力からfrontmatterの`target_repo`ごとにグループ化する。
グループ化後の件数（target_repo別の内訳）もユーザーに提示する。

ステップ2で取得した一覧のみを本セッションの処理対象として固定する。
起動以降にinboxへ追加されたファイルは本セッションでは扱わず、次回起動時の処理対象とする。

`/process-feedbacks <repo-path>`の形式で対象リポジトリパスを引数として受け取った場合は、
`dotfiles-fb list --target-repo=<repo-path>`でフィルタする。
引数なしの場合は従来通り全件を処理対象とする。

## ステップ3: target_repoグループ単位の一括処理委譲

`target_repo`グループごとに以下を実施する。グループ間は順次処理する。

1. `target_repo`のディレクトリへカレントを移す
2. 当該グループの全ファイル本文（frontmatterを除く提案本文）を結合した1つのmarkdownを
   `agent-toolkit:apply-feedback`スキルへ渡して委譲する
   - 結合形式は各ファイル本文の連結とし、各ファイルの開始位置を区切るため
     `## <filename>`形式の見出しを各本文の前に置く
   - `apply-feedback`は批判的検討・採否判定の提示・`EnterPlanMode`移行・
     `agent-toolkit:plan-mode`に従う計画作成と実装・コミットまでを担う
   - 採否確定後の承認待ちは行わない（最終承認は`ExitPlanMode`が担う）
   - グループ内の全件を1度の`apply-feedback`セッションで処理する（1件ずつ委譲しない）
3. 委譲時の追加指示として、apply-feedbackが作成する計画ファイルの`## 実行方法`へ
   採否確定後に該当する後始末手順を含めるよう明示する。
   後始末はapply-feedbackのplan-mode実装工程内で実施される
   - 採用ファイルがある場合: `dotfiles-fb adopt <filename1> <filename2> ...`を実行する
   - 不採用ファイルがある場合: `dotfiles-fb reject <filename1> <filename2> ...`を実行する

## ステップ4: 採否判別

`apply-feedback`の検討結果提示は`### <ファイル名>: <提案要約>`の見出しごとにフィードバック1件を扱う。
各見出し配下の`- 採否:`行のコロン直後の値の冒頭文言（「採用」または「不採用」）から採否を判別する。

判別が困難な場合は`AskUserQuestion`でユーザーへ確認する。

## ステップ5: サマリー提示

全グループ処理後に採用N件・不採用N件のサマリーをユーザーに提示する。
target_repo別の内訳も併記する。
