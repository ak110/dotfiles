---
name: develop-dotfiles
user-invocable: false
description: >
  dotfilesリポジトリで`make update`・`make test`・`make format`・`make setup-browser`・`make setup-pwsh`・`make test-browser`を
  実行するとき、pyfltr・MCPの`run`・`pytest`の直接実行を選ぶとき、
  専用worktreeや回収予定の検証用複製の環境を準備するとき、mise trustが必要な作業ツリーと状態ディレクトリを扱うとき、専用worktreeの変更を`atk`で動かすとき、
  画面を実描画で確認するとき、本リポジトリのテスト配置を決めるとき、テストを新規作成・編集・レビューするとき、テストやCIの所要を判定するとき、
  commit typeを判定するとき、コミットメッセージ方針や`.gitmessage`を編集するとき、
  Claude Code本体やCodex本体のバイナリを検索してホスト機能の挙動を確かめるとき、
  `agent-toolkit:review-session`の参照文書の位置を確認するとき、
  本リポジトリを対象に含むprocess-wiの巡回（all-repos統括を含む）の所要時間目標と終了条件を適用するときに起動する。
---

# dotfilesの開発手順

本スキルでは、本リポジトリにおける自動チェック、コード整形、依存関係の更新、テストの配置、コミットメッセージの方針、ホスト本体バイナリの検索手順、および振り返りで参照する文書の位置を説明する。
リリース運用は`release-dotfiles`、配布元と配布先の対応関係は`dotfiles-repo-layout`で扱う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本リポジトリを対象に含む`agent-toolkit:process-wi`の巡回（all-repos統括を含む）の所要時間目標と終了条件を適用する前、巡回の終了を判定する前 | `references/process-wi-duration.md` | | |
| 配布元と配布先の対応関係を判断する前 | `../dotfiles-repo-layout/SKILL.md` | | |
| リリース運用を判断する前 | `../release-dotfiles/SKILL.md` | | |
| 振り返りの参照文書の位置を確認する前 | `agent-toolkit:review-session/SKILL.md` | | |
| 専用worktree・回収予定の検証用複製・miseの状態ディレクトリを準備する前、専用worktreeの変更を`atk`で確認する前 | `references/worktree.md` | | |
| `make update`・`make update-actions`を実行候補にする前 | `references/dependency-update.md` | | |
| 公開前の検証を選定・実行する前 | `references/publishing.md`、`references/verification-values-execution.md` | | |
| 変更範囲の検証対象を選ぶ前 | `references/verification-values-scope.md` | | |
| pyfltr・MCPの`run`・pytestを実行する前、コード整形や検証時間の比較を行う前 | `references/verification-values-execution.md` | | |
| テストを追加・変更・レビューする前と、テストやCIの結果を成功時も含めて判定する前 | `references/verification-values-speed.md` | | |
| ローカルの`make test`が実行しないチェックを確かめる時、push前の全体検証を行う前 | `references/verification-values-publishing.md` | | |
| `agent-doc-tone`の報告へ対処する前 | `references/verification-values-tone.md` | | |
| テストの配置を決める前と、テストを新規作成・編集・レビューする前 | `references/testing.md` | | |
| 画面を実描画で確認する前、ブラウザーUI・ブラウザー経由のサーバー処理・静的ファイル・実ブラウザーテストを変更する前、ブラウザー・PowerShellの初期環境を準備する前 | `references/browser.md` | | |
| Claude Code・Codex本体のバイナリを検索する前 | `references/host-binaries.md` | | |
| コミットメッセージ方針や`.gitmessage`を編集する前 | `references/commit-message.md` | | |
| コミットメッセージのtypeを判定する時 | [commit-types.md](../../../docs/development/commit-types.md)（判定例） | | |
| 不変条件のテストの検証対象を探索する時 | `agent-basics:check-execution`の`references/verification-scope.md` | 判断 | `read:agent-toolkit:*/share/verification-operations.md` |

## 振り返りの参照文書

`agent-toolkit:review-session`の実行時にメインエージェントが参照する本リポジトリ固有の文書には、所要時間の目標やリポジトリ固有の振り返り観点が記載されている。
ファイル配置は、Claude Code環境では`~/.claude/docs/session-review-dotfiles.md`、Codex環境では`~/.codex/docs/session-review-dotfiles.md`である。
配布元の原本ファイルは`.chezmoi-source/dot_claude/docs/session-review-dotfiles.md`に置かれている。
