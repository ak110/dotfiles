---
name: process-feedbacks-loop
description: >
  ~/private-notes/feedback/inbox/配下のフィードバックとTBD回答済み項目が0件になるまで、
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

1. `dotfiles-fb show --all --status=answered --target-repo=<対象リポジトリ>`を実行する。
   出力の`### <filename>`見出し件数がfeedback全件と回答済みTBDの合計となる（空出力＝0件）。
   件数が0になるまで反復する。
   未回答TBDは`dotfiles-fb tbd-answer`（未回答TBDに回答を書き込む別サブコマンド）が別個に扱うため、
   本スキルのloop終了判定には含めない。
2. 件数が0の場合はステップ3へ進む。
3. 件数が1以上の場合、`Agent`ツール（`subagent_type: claude`）で1反復分の処理を単独foreground委譲する。
   起動プロンプトには次の指示を含める。
   - `/process-feedbacks <対象リポジトリパス>`スキルを起動して全件をapply-feedbackへ委譲すること
   - process-feedbacks完了後、`agent-toolkit:session-review`と`session-review-dotfiles`スキルを併用起動して
     振り返り提案を生成すること
   - 処理件数・採否内訳・振り返り投入件数の要約のみ返却すること（判断過程・詳細ログは返却不要）
4. 反復回数を加算して手順1へ戻る

## ステップ3: 完了サマリー

反復回数・処理済みフィードバック件数・最終的なinbox件数（0）をユーザーに報告する。

## サブエージェント委譲方針

各反復の処理は単独foreground起動の`Agent`ツール委譲へ切り替え、メイン側は反復ごとに要約のみ取得する。
`agent-toolkit/rules/claude-code.md`「サブエージェントの活用」節の不可逆な外部公開の操作禁止規範は
並列起動サブエージェント限定である。
単独foreground委譲では`git push`・`dotfiles-fb adopt`/`reject`まで委譲先で実行できる。
本スキルはapply-feedbackを直接起動しない（session-review内部の起動に委ねる）。
委譲先サブエージェント内で並列サブエージェント起動が発生する。

## dotfiles-fb process-loop CLIとの使い分け

- CLI版: 別プロセスの`claude`起動を伴い、Claude Code再起動やインタラクティブ確認の介在が可能
- 本スキル版: 単一のClaude Codeセッション内で完結し、コンテキスト継続と自動化を優先
