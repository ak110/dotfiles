---
name: spec-driven-plan
description: >
  次版ドキュメント（テーマ仕様）を基に、テーマごとの実装計画ファイル（`docs/v{next}/plans/{theme}.md`）を作成する。
  `agent-toolkit:plan-mode`を参照呼び出ししてユーザー対話・調査・整合性チェックを進め、
  ExitPlanMode直前までの工程を完遂する。引数なしで起動でき、計画ファイルが未作成のテーマを順次対話的に計画する。
# 編集時の注意点:
# 本スキルは`agent-toolkit:plan-mode`を参照呼び出しする想定だが、本スキル起動時点ではplan-modeは
# 未ロード。本スキル本文中に「plan-modeを呼ぶ」と明記し、メインエージェントがSkill呼び出しすること。
# `EnterPlanMode`を呼ばないため、plan-mode本体の編集制約規範は本スキル経由では発火しない。
# 配置先`docs/v{X}/plans/{Y}.md`はplan-modeの既定`~/.claude/plans/*.md`と異なるため、
# PreToolUseフックの計画ファイル編集ブロックは発火しない。
---

# テーマごとの実装計画作成

`agent-toolkit:spec-driven`で作成した次版ドキュメント（テーマ仕様）を入力として、テーマごとの実装計画ファイルを作成する。
ユーザー対話を伴い未確定事項を計画段階で確定させる。
本スキルは`agent-toolkit:plan-mode`の工程1〜7を参照呼び出しで実施し、工程7完了後（`ExitPlanMode`直前）で終了する。

## 1. 位置付けと前提

入力前提:

- `docs/v{next}/{theme}.md`（作業テーマドキュメント）が1件以上存在する
- `docs/v{next}/OVERVIEW.md`（次版総合ドキュメント）が存在する
- 上記の配置は`agent-toolkit:spec-driven`配下`references/spec-driven-framework.md`の既定または
  プロジェクト指定に従う

起動方法:

- 引数なしで起動する（`/agent-toolkit:spec-driven-plan`または`agent-toolkit:spec-driven`からの誘導）
- 起動時に入力前提を満たさない場合はユーザーへ報告して終了する

## 2. 対象テーマの確定

次版配置内の作業テーマドキュメントを走査し、対応する計画ファイル（`docs/v{next}/plans/{theme}.md`）が
未作成のものを処理対象とする。
処理対象テーマがなくなるまで、テーマ1件ごとに「3. ワークフロー」を繰り返す。
全テーマの計画ファイルが既に作成済みの場合は、その旨をユーザーへ報告して終了する。

## 3. ワークフロー

対象テーマ1件ごとに次を実施する。

1. `agent-toolkit:plan-mode`スキルを呼び出す
2. plan-modeの工程1〜7を実施する。ただし以下の差分を適用する
   - 計画ファイル配置を`docs/v{next}/plans/{theme}.md`へ読み替える
    （plan-mode規定の`~/.claude/plans/*.md`は使用しない）
   - `EnterPlanMode`は呼ばない（plan modeへは入らない）
   - 工程8（`ExitPlanMode`と`agent-toolkit:plan-impl`への引き継ぎ）は実施しない
3. 計画ファイル完成後、次の対象テーマへ進む

`docs/v{next}/plans/`ディレクトリが存在しない場合は計画ファイル作成前に作成する。

## 4. サブエージェント活用

メインエージェントのコンテキスト消費を抑えるため、調査・整合性チェックはサブエージェントへ委譲する。

- plan-modeの工程2の調査・列挙系grepは`Explore`サブエージェントへ委譲する
- plan-modeの工程7の整合性チェックは`plan-integrity-checker`サブエージェントとcodexレビューを並列起動する
- ユーザー対話（`AskUserQuestion`）はメインエージェント側で実施する
  - サブエージェントから直接ユーザーへ質問できないため
  - 調査委譲先で未確定事項が生じた場合はサブエージェント完了後にメイン側でまとめて`AskUserQuestion`を発行する
- サブエージェントの中断・再開コストを抑えるため、調査・整合性チェックの起動は並列バッチで1往復に集約する
  - 途中での追加質問・追加調査委譲は新規起動で行う

## 5. 次ステップ誘導

全テーマの計画ファイル作成完了後、以下のプロンプト例をユーザーへ提示して本スキルを終了する。

> `agent-toolkit:spec-driven-impl`スキルを起動してください。
