---
name: process-feedback
description: >
  ~/private-notes/feedback/inbox/配下のフィードバックを順に処理し、
  採用は対象リポジトリへ反映してファイルを削除、見送りは rejected/ へ移動する。
# 連携: agent-toolkit:apply-feedback スキルへ1件ずつ委譲する。
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

## ステップ2: 件数確認

`~/private-notes/feedback/inbox/`配下のファイル一覧を取得し、件数をユーザーに提示する。
0件の場合は「処理対象なし」を1文示して終了する。

## ステップ3: 各ファイルの処理委譲

ファイルを名前順に1件ずつ処理する。
各ファイルに対して以下を実施する。

1. ファイルを読み込み、frontmatterから`target_repo`を取得する
2. `target_repo`のディレクトリへカレントを移す
3. ファイル本文（frontmatterを除く提案本文）を`agent-toolkit:apply-feedback`スキルへ渡して完全委譲する
   - `apply-feedback`は批判的検討・採否判定の提示・ユーザー承認の取得・`EnterPlanMode`移行・
     `agent-toolkit:plan-mode`に従う計画作成と実装・コミットまでを担う

## ステップ4: 採否判別

`apply-feedback`の検討結果提示の`### 採用`配下に`- 修正理由:`で始まる行が1件以上あるかを採否判別の根拠とする。

## ステップ5: 採用時の処理

対象リポジトリへの反映コミットが完了したことを確認したうえで、以下を順に実施する。

1. `~/private-notes/feedback/inbox/<filename>`を削除する
2. `~/private-notes`で以下を順に実行する

```sh
git add feedback/inbox/<filename>
git commit -m "chore: process <filename> (adopted)"
git push
```

## ステップ6: 見送り時の処理

以下を順に実施する。

1. `~/private-notes/feedback/inbox/<filename>`を
   `~/private-notes/feedback/rejected/<filename>`へ移動する
2. `~/private-notes`で以下を順に実行する

```sh
git add feedback/inbox/<filename> feedback/rejected/<filename>
git commit -m "chore: process <filename> (rejected)"
git push
```

## ステップ7: サマリー提示

全件処理後に採用N件・見送りN件のサマリーをユーザーに提示する。
