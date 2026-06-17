---
name: process-feedbacks
description: >
  ~/private-notes/feedback/inbox/配下のフィードバックを順に処理し、
  採用は対象リポジトリへ反映してファイルを削除、見送りは rejected/ へ移動する。
# 連携: target_repoごとにグループ化し、各グループの全件をまとめて agent-toolkit:apply-feedback へ委譲する。
# フラグファイル ~/.config/agent-toolkit/feedback-inbox.enabled が存在する環境でのみ動作する。
---

# フィードバック消化

## 起動方針

`~/.config/agent-toolkit/feedback-inbox.enabled`が存在しない場合は、
フィードバック蓄積機能が無効である旨を1文示して終了する。

`~/private-notes`が存在しない場合は、手動で`~/private-notes`をcloneしてから
再度実行する旨を案内して終了する。

## ステップ1: リモート同期

`~/private-notes`で`git pull --ff-only`を実行する。

## ステップ2: 件数確認とグループ化

`~/private-notes/feedback/inbox/`配下のファイル一覧を取得し、件数をユーザーに提示する。
0件の場合は「処理対象なし」を1文示して終了する。

各ファイルを読み込み、frontmatterの`target_repo`ごとにグループ化する。
グループ化後の件数（target_repo別の内訳）もユーザーに提示する。

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
   - 採用ファイルがある場合の手順（対象リポジトリへの反映コミット完了後に実施する）:
     1. `~/private-notes/feedback/inbox/<filename>`を削除する（全採用ファイル分）
     2. `~/private-notes`で以下を順に実行する

        ```sh
        git add feedback/inbox/
        git commit -m "chore: process N feedback items (adopted)"
        git push
        ```

   - 見送りファイルがある場合の手順:
     1. `~/private-notes/feedback/inbox/<filename>`を
        `~/private-notes/feedback/rejected/<filename>`へ移動する（全見送りファイル分）
     2. `~/private-notes`で以下を順に実行する

        ```sh
        git add feedback/inbox/ feedback/rejected/
        git commit -m "chore: process N feedback items (rejected)"
        git push
        ```

   - 採用・見送りの双方が存在する場合は、両方を含めた単一コミットで反映してよい（コミットメッセージは`chore: process N feedback items (adopted: A, rejected: B)`形式とする）

## ステップ4: 採否判別

後始末はapply-feedbackのplan-mode実装工程内で完結するため、本ステップはステップ5のサマリー提示用に
採否情報を集約する工程として実施する。

`apply-feedback`の検討結果提示の`### 採用`・`### 不採用`配下から、
各ファイル（`## <filename>`見出しで対応付け）が採用か見送りかを判別する。
`apply-feedback`が出力する`### 不採用`見出し配下のファイルは見送りとして扱う。

判別が困難な場合は`AskUserQuestion`でユーザーへ確認する。

## ステップ5: サマリー提示

全グループ処理後に採用N件・見送りN件のサマリーをユーザーに提示する。
target_repo別の内訳も併記する。
