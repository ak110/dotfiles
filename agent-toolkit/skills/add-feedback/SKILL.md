---
name: add-feedback
description: >
  フィードバックまたはTBDを投入するすべての経路で起動する。
  完成済み本文の非対話投入と、通常型本文を対話で確定して投入する経路を提供する。
---

# フィードバック投入

フィードバック投入のproviderとして、本文確定、処理状況の事前確認、保存、保存後照合を担当する。
採否、実装、計画作成は担当しない。

## 入力

- 完成済み本文は問い直さず、本文、対象リポジトリ、種別、source、plan file、依存関係を受け取る
- 計画実装型では、producerが計画作成に使ったworktreeの絶対パスと計画base commitを受け取る。
  repo本体や別worktreeへ解決し直さず、同じ絶対パスを作業ディレクトリと対象リポジトリ引数へ渡す
- 通常型の主題だけを受け取った場合は利用者向け結果を現在の対話で確定する
- 予約済みcanonicalでは、plan-and-add-feedbackから受領したfilenameと生tokenを受け取る
- 期限切れ又は不正予約の回収では、process-feedbacksからfilename、観測済み世代・期限又は
  `--invalid`、完成済みTBD入力、追加依存を受け取る

## 手順

1. 全投入で[references/coordination-preflight.md](references/coordination-preflight.md)を全文読む。
   複数リポジトリの場合だけ
   [references/cross-repository-submission.md](references/cross-repository-submission.md)も全文読む
2. 各本文を単体で意味が完結する形にし、対象、観測事象、期待結果、出典、関連提案との関係を含める
3. 対象worktreeのリモートURLと完全HEADを確認する。計画実装型は完全HEADと計画base commitも照合する
4. 保存直前にactive一覧と関連項目を再取得し、事前確認の判断表を再適用する
5. 新規項目は`atk mq add`、inbox重複は`atk mq merge-inbox`で保存する。
   複数の重複項目はcanonical以外を`--supersede`へ渡して同じmutationで不採用終端へ移す
6. 予約付きprocessingは、受領したtokenが一致する所有済みcanonicalだけを`merge-inbox`へ渡す。
   token無し、不一致、通常processingは変更しない
7. processing重複は、新情報無しなら追加せず、追加差分なら依存付き追随、順序依存だけなら
   `depends_on`、独立なら通常追加とする
8. 期限切れ回収とTBD作成は、通常TBDと同じ入力検証・重複確認後、`recover-reservation`へ一括入力する。
   先に通常addでTBDを作成しない。不正予約も保存直前に不正状態を再確認して`--invalid`で回収する
9. 競合拒否時は新規TBDを残さずactiveを再取得し、最新状態へ判断表を再適用する
10. 完了表示と`atk mq show`でfilename、本文、`target_repo`、`target_commit`、`plan_file`、
    `depends_on`を入力と照合する

本文はシェルへ渡す場合にANSI-Cクォートの単一引数とし、ヒアドキュメント、パイプ、リダイレクト、
コマンド置換、言語別文字列リテラルの連結を用いない。警告又はエラーが出た場合は終了コード0でも
保存内容を再取得し、欠落を同じ経路で修復する。

## 完成条件

- 利用者向け結果に影響する未確定事項が通常型本文へ残っていない
- 保存直前のactive状態に基づいて新規、統合、追随、依存、追加無しを確定している
- 種別、metadata、本文が入力と一致し、予約を所有しない項目を変更していない
