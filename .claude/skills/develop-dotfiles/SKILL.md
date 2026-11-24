---
name: develop-dotfiles
user-invocable: false
description: >
  dotfilesリポジトリで`make update`・`make test`・`make format`・`make setup-browser`・`make setup-pwsh`・`make test-browser`を
  実行するとき、pyfltr・MCPの`run`・`pytest`の直接実行を選ぶとき、変更範囲の検証対象を選ぶとき、統合前・公開前・push前の検証を行うとき、
  専用worktreeや回収予定の検証用複製の環境を準備するとき、mise trustが必要な作業ツリーと状態ディレクトリを扱うとき、専用worktreeの変更を`atk`で動かすとき、
  画面を実描画で確認するとき、本リポジトリのテスト配置を決めるとき、テストを新規作成・編集・レビューするとき、テストやCIの所要時間を判定するとき、
  commit typeを判定するとき、コミットメッセージ方針や`.gitmessage`を編集するとき、
  Claude Code本体やCodex本体のバイナリを検索してホスト機能の挙動を確かめるとき、
  `/atk-mode review-session`の参照文書の位置を確認するとき、
  本リポジトリを対象に含むprocess-wiの巡回（all-repos統括を含む）の所要時間目標と終了条件を適用するときに起動する。
---

# dotfilesの開発手順

本リポジトリの検証と環境準備で読む資料の索引である。
作業や条件に当たる行の資料を、その操作・判断の前に全文読む。
リリース運用は`release-dotfiles`、配布元と配布先の対応は`dotfiles-repo-layout`が扱う。

## 作業と資料

| 作業・条件 | 読む資料 |
| --- | --- |
| テストを追加・変更・レビューする | `references/verification.md`「テスト配置」、`references/verification-execution.md`「テストとCIの所要基準」 |
| 変更を検証する（対象の選定、pyfltr・MCPの`run`・pytest・`make test`・`make format`の実行、統合前の最終の実行） | `references/verification.md`「変更範囲の検証の値」、`references/verification-execution.md`「検証コマンドの実行」 |
| 公開前・push前の検証、ローカルの`make test`が実行しないCIジョブの確認 | `references/verification.md`「公開前の検証」「push前のチェックとCIだけが実行するチェック」 |
| テストやCIの結果を成功時も含めて判定する | `references/verification-execution.md`「テストとCIの所要基準」 |
| `agent-doc-tone`の報告へ対処する | `references/verification-values-tone.md` |
| 専用worktree・回収予定の検証用複製・miseの状態ディレクトリを準備する、専用worktreeの変更を`atk`で動かす | `references/worktree.md` |
| `make update`・`make update-actions`を実行の候補にする | `references/dependency-update.md`、更新後の検証は`references/verification.md`「変更範囲の検証の値」 |
| 画面を実描画で確認する、ブラウザーUI・ブラウザー経由のサーバー処理・静的ファイル・実ブラウザーテストを変える、ブラウザーとPowerShellの初期環境を準備する | `references/browser.md` |
| `atk`のCLIの変更を、commit前に実プロセスで動かして確かめる | `.claude/skills/verify/SKILL.md` |
| Claude Code・Codex本体のバイナリを検索する | `references/host-binaries.md` |
| コミットメッセージ方針や`.gitmessage`を編集する | `references/commit-message.md` |
| commitのtypeを判定する | `docs/development/commit-types.md`（判定例） |
| 本リポジトリを対象に含む`agent-toolkit:atk-mode`の作業`process-wi`の巡回（all-repos統括を含む）の所要時間目標と終了条件を適用する、巡回の終了を判定する | `references/process-wi-duration.md` |
| `/atk-mode review-session`で本リポジトリ固有の振り返りの参照文書（所要時間の目標と振り返りの観点）を読む | Claude Codeでは`~/.claude/docs/session-review-dotfiles.md`、Codexでは`~/.codex/docs/session-review-dotfiles.md`（原本は`.chezmoi-source/dot_claude/docs/session-review-dotfiles.md`） |

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 不変条件のテストの検証対象を探索する時 | `agent-basics:check-execution`の`references/verification-scope.md` |
