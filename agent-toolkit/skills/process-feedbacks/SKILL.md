---
name: process-feedbacks
description: >
  対象リポジトリのフィードバックを取得・検討・適用するときに起動する。
  「フィードバックがあった」「改善提案を反映」「振り返り結果を反映」などのキーワードで起動する。
---

# フィードバック処理

activeなフィードバックを取得し、調査、採否、実装、公開、後始末まで完遂する。
新規フィードバックとTBDの投入は`agent-toolkit:add-feedback`を正本とし、本スキルへ投入手順を複製しない。

本スキルの起動中は自律モードとする。ユーザー判断が必要な事項は
`agent-toolkit/rules/01-agent.md`「協調と自律」節に従ってTBDへ永続化し、暫定判断で進める。
外部サービスの構成変更と破壊的操作は同節の確認必須例外を維持する。

## 1. 入力とreadiness

対象リポジトリの絶対パスを確定し、次を行う。

1. `atk mq list --status=active --target-repo=<repo-path>`でactive項目を取得する
2. 必要なfilenameだけを`atk mq show <filename> --target-repo=<repo-path>`で取得する
3. `plan_file`を持つfeedbackを計画実装型、それ以外を通常型とする。本文から型を推測しない
4. `depends_on`が全て終端し、TBDは回答済みで、frontmatterと計画ファイルが有効な項目をreadyとする
5. readyなinbox項目を`atk mq start-processing`でprocessingへ移す。
   processing項目は実体を確認し、完了済み工程を再実行せず未完了工程から再開する

期限内の予約付きprocessingは別producerの所有項目としてreadyから除外する。期限切れは世代と期限を記録し、
plan file、関連process、worktreeから所有工程を確認する。終了済みと確定した場合だけ、実行主体が
`agent-toolkit:add-feedback`をSkill機能で起動して観測値を渡し、CAS回収後にinboxから再評価する。
継続中なら所有者による更新を待ち、自身はtokenを利用しない。判定不能なら完成済みTBD入力とCAS値を
同時に渡し、CAS成立後だけTBD作成、依存追加、inbox遷移を一括実行させる。

不正予約は`show`で再取得し、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動してfilenameと
`--invalid`回収要求を渡す。orphan又は対応不一致のcompanionだけはtarget repo filter無しで取得し、
内部metadataの元target repoが処理中repoと一致することを
検証する。競合拒否時はactiveを再取得し、有効予約なら保留、不正のままなら回収経路へ戻る。
構文解析不能なfrontmatterは既存のfrontmatter修復経路へ送る。

欠落依存、自己依存、循環、frontmatter破損、計画ファイル消失は修復対象とする。
過去の`queue_schedule.dependency`は読取互換だけ維持し、新規記録へ用いない。
複数の計画実装型を扱う場合は`references/plan-impl-feedback-flow.md`を全文読む。

## 2. 調査と採否

通常型は次の順で扱う。

1. `references/content-adjustment.md`と`references/review-checklists.md`を全文読む
2. 原文、現行実装、関連規範、履歴、既存の成功経路を調査する
3. バグ・障害・回帰では実行主体が`agent-toolkit:bugfix`をSkill機能で起動する
4. 横断調査を委譲する場合は`agent-toolkit:delegation`に従い、
   `references/explore-template.md`だけを受信者用taskとして渡す
5. 単一要求は採用または不採用、独立した複数要求は採用、部分採用、不採用で判定する。
   原文の単一要求を薄めて部分採用しない
6. `references/decision-format.md`に従って採否と根拠を記録する

外部ツール、ライブラリ、サービスの挙動を成果物へ転記する前に、一次資料または実装で裏付ける。
技術的に確定できない事項とユーザー判断は保留へ送る。

## 3. 保留

保留時は`references/hold-with-tbd-inject.md`を全文読み、解除条件と再開情報を永続化する。
processing項目は`atk mq return-to-inbox`でinboxへ戻す。
回答済みTBDの能動的なpoll、内部待機ループ、同一セッションでの時限待機は行わない。

readyな採用項目が無ければ、保留状態を維持して「6. 振り返りと終了」へ進む。
process-loopがactive状態の変化を検出し、readiness成立後に新しいセッションを起動する。

## 4. 実装と公開

- 通常型の採用項目は実行主体が`agent-toolkit:plan-mode`をSkill機能で起動し、調査済み事実と採否を渡す
- 計画実装型は計画ファイルを正本として実装する
- commit前に実行主体が`agent-toolkit:commit`をSkill機能で起動する
- 実装と二系統reviewの完了後、呼び出し元がpushとCI通過確認を完遂する
- 計画の完了条件を満たした対象だけを後始末へ進める

作業中に独立した新規改善を発見した場合は、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、
完成済み本文と対象リポジトリを渡す。投入コマンドを本スキルから直接構成しない。

## 5. 後始末

- 不採用: 判定確定後に`atk mq reject <filename> --note=<理由>`を実行する
- 採用: 対象commitのpush完了後に
  `atk mq adopt <filename> --note=<反映概要> --commit=<完全長SHA>`を実行する
- 回答済みTBD: 回答を反映した処理の完了後に同じ採用経路で終端させる

`adopt`と`reject`は、同じ対象リポジトリに回答済みのactive TBDが残る場合、
queue lock内の変異直前検査で停止する。通知の有無を処理可否の根拠にしない。
各コマンドの保存結果を再取得し、対象、採否、note、commitを照合する。

## 6. 振り返りと終了

全ready項目の処理又は保留永続化後に、実行主体が`agent-toolkit:session-review`をSkill機能で起動する。
振り返りで生成した提案は同スキルがadd-feedbackを起動して投入する。完了後に実行主体が
`agent-toolkit:exit-session`をSkill機能で起動する。
