---
name: apply-feedback-finish
description: >
  `agent-toolkit:apply-feedback`の実装・レビュー完遂後に残る後続工程
  （コミット・push・CI通過確認・adopt/reject/tbd-adopt・振り返り・exit-session）を
  段階的に実施する。`agent-toolkit:process-feedbacks`・`atk fb process-loop`から
  後続工程の到達先として参照される。単独のユーザー起動も可とする。
---

# フィードバック処理の後続工程

`agent-toolkit:apply-feedback`のステップ5（計画作成と実行）完遂後に、
本スキルへ進んで残工程を1つずつ実施する。
各工程は前工程の完了を条件として次工程へ進む。
工程の総量・所要時間は着手可否の判断材料にしない
（根拠は`agent-toolkit/rules/01-agent.md`「品質最優先」節冒頭が参照する完遂成立モデル）。

## 工程1: 全計画のコミット完了確認

未コミット変更が残っていれば当該計画ファイルの`## 実行方法`に従い
`agent-toolkit:commit`スキルの規約でコミットを完遂してから次工程へ進む。
1計画ファイルあたり1コミットの原則を維持する。

## 複数計画時の実行モデル

`plan_group`が独立している複数計画（未設定feedbackが個別の1計画へ、同一値feedback群が
統合1計画へそれぞれ生成された結果、計画が複数となる場合）が存在するときは以下に従う。
本スキルは全計画の実装完遂後に1回だけ起動する。
工程2のpushは1回で全コミットをまとめて公開する。
工程4の採否確定（adopt/reject/tbd-adopt）は計画単位で行い、各計画のcommit shaを
`--commit`へ個別に指定して実施する。

## 工程2: push

`git push`を実行する。リモート名・ブランチ名は明示せず単独で呼び出す。

## 工程3: CI通過確認

`agent-toolkit:commit`スキル「push後のCI通過確認」節の手順に従い、
push対象shaのCI runが全て成功するまで確認する。

## 工程4: 採否確定の後始末

`agent-toolkit:apply-feedback`による批判的な検討の結果に基づき、対象ファイルを後始末する。

- feedback側の採用ファイル: `atk fb adopt <filename...> --note <概要> --commit <sha>`
- feedback側の不採用ファイル: `atk fb reject <filename...> --note <不採用理由> --commit <sha>`
- TBD側の回答済み採用ファイル: `atk fb tbd-adopt <filename...> --note <概要> --commit <sha>`
- `--note`・`--commit`の詳細規定は`agent-toolkit:apply-feedback`配下
  `references/decision-format.md`「後始末コマンドの引数」節に従う
- 保留ファイルは後始末コマンドを実行しない
  （`atk fb`は次回`show`で自動的に再評価対象として提示する）

## 工程5: 振り返り

dotfiles個人環境向け拡張`session-review-dotfiles`スキルが利用可能な場合は先に起動し、
その後に`agent-toolkit:session-review`スキルを起動する。
`session-review-dotfiles`が利用できない場合は`agent-toolkit:session-review`スキルのみを起動する。
`session-review-dotfiles`の起動失敗は本工程の完遂条件から除外し、
`agent-toolkit:session-review`スキルの完遂を必須条件とする。
振り返り工程完遂前に完了を示す応答は発行しない。

## 工程6: セッション終了

自律実行系CLI（`atk fb process-loop`等）から間接起動されたセッションでは、
工程5の完遂後に`agent-toolkit:exit-session`を呼び出す。
ユーザー起動のセッションでは`exit-session`の起動条件
（`agent-toolkit/skills/exit-session/SKILL.md`「起動条件」節）に従い、非該当時は本工程を起動しない。
