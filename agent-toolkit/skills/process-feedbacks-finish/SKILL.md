---
name: process-feedbacks-finish
description: process-feedbacksからの後続工程またはユーザー手動起動。
---

# フィードバック処理の後続工程

`agent-toolkit:process-feedbacks`のステップ5（計画作成と実行）完遂後、
メイン側が`plan-impl-executor`の完了報告を受領した時点で本スキルを起動する。
起動後は各工程を1つずつ実施する。
本スキルの呼び出しは常にメイン側の専任とし、`plan-impl-executor`側の`## 実行方法`本文へは記載しない。
各工程は前工程の完了を条件として次工程へ進む。
工程の総量・所要時間は着手可否の判断材料にしない
（根拠は`agent-toolkit/rules/01-agent.md`「品質最優先」節冒頭が参照する完遂成立モデル）。
対象リポジトリにコード変更が無い場合（全件不採用・全件保留等）も本スキルを起動する。
コミット対象・変更の有無を理由とする終了宣言は縮退表明に該当し禁止する
（`agent-toolkit/rules/01-agent.md`「品質最優先」節「完遂原則」参照）。
工程1・工程2は該当作業が無い場合も当該工程内でその旨確認したうえで次工程へ進む。
工程3は工程2でpushが発生した場合のみpush対象shaのCI通過確認を実施し、
工程2で変更なしのため実際のpushが発生しなかった場合は当該旨を確認したうえで次工程へ進む。
工程4〜6は変更有無に関わらず必ず実施する。

## 工程1: 全計画のコミット完了確認

未コミット変更が残っていれば当該計画ファイルの`## 実行方法`に従い
`agent-toolkit:commit`スキルの規約でコミットを完遂してから次工程へ進む。
1計画ファイルあたり1コミットの原則を維持する。

## 工程2: push

`git push`を実行する。リモート名・ブランチ名は明示せず単独で呼び出す。

## 工程3: CI通過確認

`agent-toolkit:commit`スキル「push後のCI通過確認」節の手順に従い、
push対象shaのCI runが全て成功するまで確認する。

## 工程4: 採否確定の後始末

`agent-toolkit:process-feedbacks`による批判的な検討の結果に基づき、対象ファイルを後始末する。

- feedback側の採用ファイル: `atk fb adopt <filename...> --note=<概要> --commit=<sha>`
- feedback側の不採用ファイル: `atk fb reject <filename...> --note=<不採用理由> --commit=<sha>`
- TBD側の回答済み採用ファイル: `atk tb adopt <filename...> --note=<概要> --commit=<sha>`
- `--note`・`--commit`の詳細規定は`agent-toolkit:process-feedbacks`配下
  `references/decision-format.md`「後始末コマンドの引数」節に従う
- 保留ファイルは後始末コマンドを実行しない
  （`atk fb`は次回`show`で自動的に再評価対象として提示する）

## 工程4.5: 連鎖feedbackの自律投入

対象は採用フィードバック本文が`agent-toolkit/skills/add-feedback/SKILL.md`「完了条件と連鎖feedback」
節の書式（`### 完了条件と連鎖feedback`見出しで始まるブロック）を含む場合とする。
本工程では次を順に実施する。

1. 元feedback本文の「実装完了条件」欄に列挙された観測条件を取得する
   - 各条件を元feedback記載の確認手段で1件ずつ実行する
   - 確認手段の例は`git tag`存在確認・`gh run list --commit=<sha>`のconclusion確認・
     PyPI・GitHub Release等の直接観測手段とする
   - 待機上限は既定30分とする。呼び出し元起動プロンプトが待機上限を明示指定した場合はその値を採用する
   - 上限内に全条件が充足された場合は手順2へ進む
   - 列挙された条件はすべて必須項目として扱う
   - プロジェクト個別事情は元feedback側で条件選定する
     （選定例: PyPIパッケージ配布・GitHub Release作成・タグのみリリースなど）
2. 待機上限内に未充足の場合、TBDへ永続化する
   - `atk tb add --scope=chain-feedback`で記録する
   - 記録項目は未充足条件・確認手段・元feedback IDとする
   - 記録後は本工程を終了する
   - TBDは通常のTBD処理サイクルで扱う設計とする
3. 全ての完了条件が充足された時点で、後続feedbackを投入する
   - 元feedback本文の「連鎖投入する後続feedbackの完全な内容」欄から本文全文を取り出す
   - 取り出した本文を`agent-toolkit:add-feedback`スキルに従い対応target_repoへ投入する
   - 投入方法は`atk fb add`とする
   - 投入本文に「関連feedback: <元feedback ID>」形式のトレーサビリティ表記が含まれることを確認する
4. 該当節を含む採用フィードバックが無い場合は本工程の対象なしとして次工程へ進む

本工程は`agent-toolkit:process-feedbacks`「ステップ2.5: 採否判定前の網羅調査」節の保留判定
（採用前提の充足待ち・processing残置）とは適用対象が異なる。保留判定は未充足の前提を理由に
採否確定前で待機する運用であり、本工程は採用確定済みフィードバックの完了条件を検出した後に
後続feedbackを追加投入する運用である。

## 工程5: 振り返り

dotfiles個人環境向け拡張`session-review-dotfiles`スキルが利用可能な場合は先に起動し、
その後に`agent-toolkit:session-review`スキルを起動する。
`session-review-dotfiles`が利用できない場合は`agent-toolkit:session-review`スキルのみを起動する。
`session-review-dotfiles`の起動失敗は本工程の完遂条件から除外し、
`agent-toolkit:session-review`スキルの完遂を必須条件とする。
振り返り工程完遂前に完了を示す応答は発行しない。

## 工程6: セッション終了

工程5の完遂後に`agent-toolkit:exit-session`を呼び出す。
呼び出し元・起動元の判定分岐は持たず、常に本工程を実施する。
