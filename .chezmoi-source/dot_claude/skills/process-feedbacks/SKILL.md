---
name: process-feedbacks
description: >
  ~/private-notes/feedback/inbox/配下のフィードバックを順に処理し、
  採用は対象リポジトリへ反映してadopted/サブディレクトリへ移動、不採用はrejected/サブディレクトリへ移動する。
# 連携: 対象リポジトリのフィードバック全件をまとめて agent-toolkit:apply-feedback へ委譲する。
---

# フィードバック消化

`dotfiles-fb`の全サブコマンド（`add`・`list`・`adopt`・`reject`・`rm`・`edit`・`commit`）が内部で`git pull --ff-only`を実行するため、手動での`git pull`実行は不要とする。
`adopt`・`reject`はさらに内部でfeedback-inboxリポジトリ側のcommit/pushまで実行するが、対象リポジトリ（dotfiles等）側のcommit/pushは別途必要とする。

## ステップ1: 件数確認

`/process-feedbacks <repo-path>`の形式で対象リポジトリパスを引数として受け取った場合は当該パスを対象リポジトリとして扱う。
引数なしの場合は`git rev-parse --show-toplevel`で取得した現リポジトリパスを対象リポジトリとして扱う（既定）。

`dotfiles-fb list --target-repo=<対象リポジトリパスまたは正規化リモートURL>`を実行し、件数を1文でユーザーに提示する。
正規化リモートURLは`host/owner/repo`形式とする。
0件の場合は「処理対象なし」と示して終了する。
なお、feedback-inbox無効環境では`dotfiles-fb list`がフラグファイル不在を検出して非ゼロ終了する。
その場合は標準エラー出力のエラーメッセージをユーザーへ提示して終了する。

ステップ1で取得した一覧のみを本セッションの処理対象として固定する。
起動以降にinboxへ追加されたファイルは本セッションでは扱わず、次回起動時の処理対象とする。

## ステップ2: 全件をapply-feedbackへ一括委譲

1. 対象リポジトリのディレクトリへカレントを移す
2. 全ファイル本文（frontmatter保持）を結合した1つのmarkdownを
   `agent-toolkit:apply-feedback`スキルへ渡して委譲する
   - 結合形式は各ファイル本文の連結とし、各ファイルの開始位置を区切るため
     `## <filename>`形式の見出しを各本文の前に置く
   - frontmatterは保持することで`source: session-review`などの投入元情報をapply-feedback側で参照できるようにする
   - `apply-feedback`は批判的検討・採否判定・計画作成・実装・コミット・後始末（adopt/reject）まで担う
   - 後始末（adopt/reject）はファイルを物理削除せず`adopted/`または`rejected/`サブディレクトリへ移動して履歴を保持する
   - 全件を1度の`apply-feedback`セッションで処理する（1件ずつ委譲しない）
3. 委譲時の追加指示として、apply-feedbackが作成する計画ファイルの`## 実行方法`へ
   採否確定後に該当する後始末手順を含めるよう明示する。
   後始末はapply-feedbackのplan-mode実装工程内で実施される
   - `dotfiles-fb adopt`・`dotfiles-fb reject`は対象リポジトリのレビュー完遂・`git push`完了後に実行する。両コマンドとも内部でfeedback-inboxリポジトリ側のcommit/pushまで実行するため、対象リポジトリ側がレビュー指摘で巻き戻った場合にフィードバック管理側だけが先行公開され整合性が崩れることを避ける
   - 採用ファイルがある場合: `dotfiles-fb adopt <filename1> <filename2> ...`を実行する
   - 不採用ファイルがある場合: `dotfiles-fb reject <filename1> <filename2> ...`を実行する

## ステップ3: サマリー提示

apply-feedback完了後、採用N件・不採用N件のサマリーをユーザーに提示する。
