---
name: overhaul-project
description: >
  プロジェクト全体を網羅的に点検し、コード改善（リファクタリング）・ドキュメント整備・足元整備の3観点で
  まとめて改善するワークフロー。ユーザー手動起動専用（`/agent-toolkit:overhaul-project`）。
disable-model-invocation: true
# 編集時の注意点:
# 本スキルはユーザー手動起動専用。disable-model-invocation: trueでモデル側からの自動起動を抑止する。
# plan-mode必須の独立スキルで、3観点（コード・ドキュメント・足元）の網羅点検を担う。
# 各観点の品質基準は呼び出し先スキル（coding-standards・writing-standards・agent-standards・review-standards）
# へ委ね、本スキル本体には統合ワークフロー固有の進め方のみを記述する。
---

# プロジェクト全体の徹底改善

## 目的とスコープ

ユーザー手動起動を契機に、コード改善・ドキュメント整備・足元整備の3観点で
プロジェクト全体の改善候補を洗い出し、計画化と実装まで進める。

## 起動条件と前提

- ユーザー手動起動専用（`/agent-toolkit:overhaul-project`）
- plan-mode必須。起動直後に`agent-toolkit:plan-mode`を呼び出す
- 改善判定および記述ルールは呼び出し先スキルへ委ねる
- リファクタリングで公開インターフェース変更などの破壊的変更が発生する場合、計画段階でユーザー確認を得る

## 改善観点

| 観点 | 対象範囲 | 評価基準 |
| --- | --- | --- |
| コード改善 | ソースコード・テストコード | `agent-toolkit:coding-standards`・`agent-toolkit:writing-standards`（コメント） |
| ドキュメント整備 | 一般ドキュメント・README・技術文書・API文書 | `agent-toolkit:writing-standards` |
| 足元整備 | 設定ファイル群とエージェント向け文書群（下記参照） | `agent-toolkit:agent-standards`・pyfltr推奨設定・各ツール公式ドキュメント |

足元整備の対象範囲は次の2系統。

- 設定ファイル
  - `Makefile`・`mise.toml`・`pyproject.toml`
  - `.pre-commit-config.yaml`・`.github/workflows/`配下
  - 各種lint設定（`.textlintrc.yaml`・`.markdownlint-cli2.yaml`・`ruff`設定など）
- エージェント向け文書
  - `AGENTS.md`・`CLAUDE.md`
  - `.claude/rules/`配下・`.claude/skills/`配下・`.agents/`配下

ドキュメント整備の追加観点は次の通り。

- 既存ドキュメントの改善・削除・統合・修正に加え、不足ドキュメントの追加も対象に含める
- `docs/`配下にコーディングレベルの詳細記述やエージェント向け指示が混入している場合、
  `.claude/skills/`・`.claude/rules/`・`CLAUDE.md`等への移動を検討する

## 進め方

1. `agent-toolkit:plan-mode`を呼び出し、計画ファイル作成プロセスへ入る
2. 3観点ごとに並列スキャンを実施し、改善候補を洗い出す
3. 候補一覧をユーザーへ提示し、優先順位・採否・破壊的変更の取り扱いを確定する
4. 確定した方針を計画ファイルへ転記し、`ExitPlanMode`で承認を得る
5. `agent-toolkit:plan-impl`へ引き継いで実装・検証・コミットを進める
6. レビューは`agent-toolkit:careful-review`へ引き継ぐ

## サブエージェント活用

3観点を独立タスクに分割し、Exploreサブエージェントで並列スキャンする。

- 観点ごとに別エージェントを起動し、独立する2件以上は`run_in_background=true`で同時実行する
- モデル選定はagent.md「サブエージェントの活用」節の判断軸に従う
- 実装フェーズの委譲判断・並列起動方針は`agent-toolkit:plan-impl`の標準ルールに従う

## 既存スキルとの連携

- `agent-toolkit:plan-mode` — 起動直後に必ず呼び出す
- `agent-toolkit:coding-standards` — コード改善に着手するとき
- `agent-toolkit:writing-standards` — ドキュメント整備およびコメント記述時
- `agent-toolkit:agent-standards` — エージェント向け文書整備時
- `agent-toolkit:review-standards` — レビュー実施時
- `agent-toolkit:pyfltr-usage` — 足元整備時
- `agent-toolkit:plan-impl` — `ExitPlanMode`後の実装フェーズへ引き継ぐ
- `agent-toolkit:careful-review` — レビューフェーズへ引き継ぐ

## 参考リソース

- pyfltr推奨設定（Python向け）: <https://ak110.github.io/pyfltr/guide/recommended/>
- pyfltr推奨設定（非Python向け）: <https://ak110.github.io/pyfltr/guide/recommended-nonpython/>
- 採用済みlinter・formatter・タスクランナー・CI設定の公式ドキュメント
