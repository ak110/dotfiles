---
name: add-feedback
description: >
  フィードバックまたはTBDを投入するすべての経路で起動する。
  完成済み本文の非対話投入と、通常型本文を対話で確定して投入する経路を提供する。
---

# フィードバック投入

本スキルは、フィードバックまたはTBDを投入する手順を提供する。

## 入力

- 完成済み本文は問い直さず、本文、対象リポジトリ、種別、source、plan file、依存関係を受け取る
- 計画実装型では、producerが計画作成に使ったworktreeの絶対パスと計画base commitを受け取る。
  repo本体や別worktreeへ解決し直さず、同じ絶対パスを作業ディレクトリと対象リポジトリ引数へ渡す
- 通常型の主題だけを受け取った場合は利用者向け結果を現在の対話で確定する
- 計画実装型では、計画へ吸収してrejectedへ移した元項目のfilenameをproducerから受け取る

## 手順

1. 全投入で`references/coordination-preflight.md`を全文読む。
   複数リポジトリの場合だけ
   `references/cross-repository-submission.md`も全文読む
2. 各本文を単体で意味が完結する形にし、対象、観測事象、期待結果、出典、関連提案との関係を含める
3. 対象worktreeのリモートURLと完全HEADを確認する。計画実装型は完全HEADと計画base commitも照合する
4. 保存直前にactive一覧と関連項目を再取得し、事前確認の判断表を再適用する。
   競合で拒否された場合も同様に再取得して再適用する
5. 重複inboxは、同じ対象リポジトリにある回答済みTBDを先に終端してから、
   `atk mq reject <filename> --if-inbox --note=<移管理由>`で終端する。
   状態競合で拒否された場合はactive一覧を再取得し、processing項目を変更しない
6. 新規項目は`atk mq add`で保存し、計画実装型は吸収元filenameを本文へ記録する
7. processing重複は、新情報無しなら追加せず、追加差分なら依存付き追随、順序依存だけなら
   `depends_on`、独立なら通常追加とする
8. 完了表示と`atk mq show`でfilename、本文、`target_repo`、`target_commit`、`plan_file`、
   `depends_on`を入力と照合する

本文へ引用符・改行を含む場合は、本文をファイルへ保存し`atk mq add --body-file <path>`で渡す
（複数回指定可。位置引数との併用は拒否される）。警告又はエラーが出た場合は
終了コード0でも保存内容を再取得し、欠落を同じ経路で修復する。

## 完成条件

- 利用者向け結果に影響する未確定事項が通常型本文へ残っていない
- 保存直前のactive状態に基づいて新規、終端、追随、依存、追加無しを確定している
- 種別、metadata、本文が入力と一致し、processing項目を変更していない
