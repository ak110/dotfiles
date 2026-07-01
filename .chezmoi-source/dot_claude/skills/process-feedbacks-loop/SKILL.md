---
name: process-feedbacks-loop
description: >
  ~/private-notes/feedback/inbox/配下のフィードバックが0件になるまで、
  process-feedbacksによる消化とsession-review-dotfilesによる振り返りの投入を自動的に繰り返す。
  メインのコンテキスト消費を抑えるためサブエージェント委譲を可能な限り活用する。
# 連携先スキル: agent-toolkit:apply-feedback・process-feedbacks・session-review-dotfiles・
# agent-toolkit:session-reviewを組み合わせて呼び出す。
---

# フィードバック消化ループ

`dotfiles-fb process-loop` CLIのスキル版。手動操作なしでフィードバック消化と振り返り投入を反復する。

## ステップ1: 対象リポジトリの確定

`/process-feedbacks-loop <repo-path>`形式で引数を受け取った場合は当該パスを対象リポジトリとする。
引数なしの場合は`git rev-parse --show-toplevel`で取得した現リポジトリパスを対象リポジトリとする。

## ステップ2: 反復実行

inbox件数が0になるまで反復する。反復回数の上限は設けない。

1. `dotfiles-fb list --target-repo=<対象リポジトリ>`でinbox件数を取得する。
2. 件数が0の場合はステップ3へ進む。
3. 件数が1以上の場合、`process-feedbacks`スキルを起動して全件をapply-feedbackへ委譲する。
   （process-feedbacksが採用件のadopt・不採用件のrejectまで反映を完了させる）。
4. process-feedbacks完了後、`agent-toolkit:session-review`と`session-review-dotfiles`スキルを併用して起動し、
   本セッションの振り返り提案を生成する。
   session-reviewが自身の内部で`agent-toolkit:apply-feedback`を経由してinbox投入まで担うため、
   本スキルは追加でapply-feedbackを直接起動しない。
   次の反復の手順1でinbox件数を再取得すれば新規投入分が処理対象へ含まれる。
5. 反復回数を加算して手順1へ戻る。

## ステップ3: 完了サマリー

反復回数・処理済みフィードバック件数・最終的なinbox件数（0）をユーザーに報告する。

## サブエージェント委譲方針

各反復のprocess-feedbacks・session-review起動はメインが直接実行する
（各スキルは独立したサブエージェント委譲を内包しており、二重委譲はコンテキスト浪費を招く）。
本スキルはapply-feedbackを直接起動しない（session-review内部の起動に委ねる）。
各スキル内部で並列サブエージェント起動が発生する。

## dotfiles-fb process-loop CLIとの使い分け

- CLI版: 別プロセスの`claude`起動を伴い、Claude Code再起動やインタラクティブ確認の介在が可能
- 本スキル版: 単一のClaude Codeセッション内で完結し、コンテキスト継続と自動化を優先
