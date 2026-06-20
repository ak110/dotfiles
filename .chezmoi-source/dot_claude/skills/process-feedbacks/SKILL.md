---
name: process-feedbacks
description: >
  ~/private-notes/feedback/inbox/配下のフィードバックを順に処理し、
  採用は対象リポジトリへ反映してファイルを削除、不採用は rejected/ へ移動する。
# 連携: 対象リポのフィードバック全件をまとめて agent-toolkit:apply-feedback へ委譲する。
# フラグファイル ~/.config/agent-toolkit/feedback-inbox.enabled が存在する環境でのみ動作する。
---

# フィードバック消化

## 起動方針

`~/.config/agent-toolkit/feedback-inbox.enabled`が存在しない場合は、
フィードバック蓄積機能が無効である旨を1文示して終了する。

`~/private-notes`が存在しない場合は、手動で`~/private-notes`をクローンしてから
再度実行する旨を案内して終了する。

## ステップ1: 事前準備の確認

`dotfiles-fb`の全サブコマンド（`add`・`list`・`adopt`・`reject`・`rm`・`edit`・`commit`）が内部で`git pull --ff-only`を実行するため、手動での`git pull`実行は不要とする。

## ステップ2: 件数確認

`/process-feedbacks <repo-path>`の形式で対象リポジトリパスを引数として受け取った場合は当該パスを対象リポとして扱う。
引数なしの場合は`git rev-parse --show-toplevel`で取得した現リポジトリパスを対象リポとして扱う（既定）。

`dotfiles-fb list --target-repo=<対象リポパス>`を実行し、件数を1文でユーザーに提示する。
0件の場合は「処理対象なし」と示して終了する。

ステップ2で取得した一覧のみを本セッションの処理対象として固定する。
起動以降にinboxへ追加されたファイルは本セッションでは扱わず、次回起動時の処理対象とする。

別リポジトリ対象が必要な場合は`/process-feedbacks <repo-path>`で明示する。

## ステップ3: 全件をapply-feedbackへ一括委譲

1. `target_repo`のディレクトリへカレントを移す
2. 全ファイル本文（frontmatter保持）を結合した1つのmarkdownを
   `agent-toolkit:apply-feedback`スキルへ渡して委譲する
   - 結合形式は各ファイル本文の連結とし、各ファイルの開始位置を区切るため
     `## <filename>`形式の見出しを各本文の前に置く
   - frontmatterは保持することで`source: session-review`などの投入元情報をapply-feedback側で参照できるようにする
   - `apply-feedback`は批判的検討・採否判定・計画作成・実装・コミット・後始末（adopt/reject）まで担う
   - 全件を1度の`apply-feedback`セッションで処理する（1件ずつ委譲しない）
3. 委譲時の追加指示として、apply-feedbackが作成する計画ファイルの`## 実行方法`へ
   採否確定後に該当する後始末手順を含めるよう明示する。
   後始末はapply-feedbackのplan-mode実装工程内で実施される
   - 採用ファイルがある場合: `dotfiles-fb adopt <filename1> <filename2> ...`を実行する
   - 不採用ファイルがある場合: `dotfiles-fb reject <filename1> <filename2> ...`を実行する

## ステップ4: サマリー提示

apply-feedback完了後、採用N件・不採用N件のサマリーをユーザーに提示する。
