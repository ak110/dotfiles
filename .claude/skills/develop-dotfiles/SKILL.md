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
  `agent-toolkit:review-session`の参照文書の位置を確認するときに起動する。
---

# dotfilesの開発手順

本スキルでは、本リポジトリにおける自動チェック、コード整形、依存関係の更新、テストの配置、コミットメッセージの方針、ホスト本体バイナリの検索手順、および振り返りで参照する文書の位置を説明する。
リリース運用は`release-dotfiles`、配布元と配布先の対応関係は`dotfiles-repo-layout`で扱う。

## process-wiの所要時間と終了条件

本リポジトリを対象に含む`agent-toolkit:process-wi`の巡回は、件数によらず、選定から公開・CI・後始末・報告までを原則180分以内に収める。
別cwdのall-repos統括にも適用し、準備済みの全件、例外のないレーン上限と必要な品質工程を保つ。
計測範囲は[セッションの所要時間目標](../../../.chezmoi-source/dot_claude/docs/session-review-dotfiles.md#セッションの所要時間目標)に従う。
レーン内の先行準備・確定HEADごとの独立文脈レビュー・共有2検証枠への早期投入と成功再利用を使う。
内部担当の読取・実装・レビューを合わせた同時数Cを資源・サービス・総費用の比較から選ぶ。
配分の判断は`agent-toolkit:process-wi/references/selection-procedure-allocation.md`「調査とレーン分け」に従う。
内部群は`agent-toolkit:delegation/references/routing-units.md`「委譲単位の確定」に従う。

毎回の終了前に、次回候補全体と今回の未確定項目を現キューへ対応付け、代表群の実測と残量から、選定・公開CI・後始末や代表外の実機・別ホストの前提を含む最長経路を再計算する。
合理的なCと2検証枠で180分以内の見込みが成立する根拠を得るまで、必要な改善の実装・公開・検収を同じ巡回で続ける。
局所の成功、未実測の予算、打切り値の完走速度への外挿と、未確認工程を0秒にする計算は、達成見込みの根拠へ使わない。
進行中の30分点検と所要受領でも、巡回開始から180分の絶対完了時刻に対して、律速群の量・実測速度・待ち・依存を比べ、必要な組み直しを実際に指示する。

同じ品質での分割・並行化・先行レビュー・共通仕事の集約を比べても、資源/費用に合理的なCの下で時間内に収まらないと裏付けられた巨大WIだけは、ユーザーの具体的対象への事前承認により時間目標から除外できる。
判定材料と承認経路は前記の「調査とレーン分け」に従う。
未承認・拒否時は通常の時間対象へ残し、承認後も全件の実装・必要品質・公開を保つ。
全体壁時計、承認対象の作業/待ちと通常対象の最長経路を分ける。
並行した通常仕事の時間は保持し、時間除外は巨大WIだけが全体を遅らせた最長経路上の寄与へ限る。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| `agent-toolkit:process-wi`の所要時間目標を適用する前 | `agent-toolkit:process-wi/SKILL.md` | | |
| 配布元と配布先の対応関係を判断する前 | `../dotfiles-repo-layout/SKILL.md` | | |
| リリース運用を判断する前 | `../release-dotfiles/SKILL.md` | | |
| 振り返りの参照文書の位置を確認する前 | `agent-toolkit:review-session/SKILL.md` | | |
| 進捗と時間目標を見直す前 | `agent-basics:writing-standards/references/quality-principles-qcd.md` | | |
| 専用worktree・回収予定の検証用複製・miseの状態ディレクトリを準備する前、専用worktreeの変更を`atk`で確認する前 | `references/worktree.md` | | |
| `make update`・`make update-actions`を実行候補にする前 | `references/dependency-update.md` | | |
| 公開前の検証を選定・実行する前 | `references/publishing.md` | | |
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
