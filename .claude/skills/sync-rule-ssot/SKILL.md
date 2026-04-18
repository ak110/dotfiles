---
name: sync-rule-ssot
description: dotfilesリポジトリでagent-toolkitルールファイルを追加・削除・リネームする際に必ず使う。配布元ディレクトリがSSOTで、chezmoi/claudize経由の配布はディレクトリごと同期するため自動追従するが、`install-claude.sh`/`install-claude.ps1`のファイルリスト配列のみ手動更新が必要。「ルールを追加」「ルールを削除」「`rules/agent-toolkit`を変更」などのキーワードで自動トリガーしてよい。
user-invocable: false
---

# agent-toolkit ルールの SSOT 同期

## 目的

`~/dotfiles/` リポジトリのagent-toolkitルール配布は、配布元ディレクトリ `.chezmoi-source/dot_claude/rules/agent-toolkit/` がSSOTとなる。
chezmoi経由の配布と `claudize` コマンドはディレクトリ全体を同期するため、ファイルの追加・削除・リネーム時にそれら経路のコード修正は不要である。

ただし `install-claude.sh` / `install-claude.ps1` はリモートインストーラー（`curl ... | bash`のワンライナー実行を想定）で、配布経路を介さずGitHub Raw経由で個別ファイルを取得する。
依存を減らすためリポジトリを丸ごとcloneせず、配布対象のファイル名を配列としてスクリプト内に持つ構造になっている。
この1箇所のみ、配布対象ファイルの増減に合わせて手動で更新する必要がある。

## 前提確認

作業を始める前に、編集するリポジトリが `~/dotfiles`（または同等の本家dotfilesチェックアウト）であるか確認する。
他プロジェクトの `.claude/rules/` は配布先であり原本ではない。
本スキルは本家dotfilesリポジトリの編集でのみ使う。他プロジェクトで`.claude/rules/`配下を編集しても、次回の配布で上書きされるため恒久化しない。

配布元ディレクトリ: `.chezmoi-source/dot_claude/rules/agent-toolkit/`

## 追加・リネーム手順

1. 配布元ファイルを作成（またはリネーム）する — `.chezmoi-source/dot_claude/rules/agent-toolkit/<rule>.md`

2. `install-claude.sh` の `FILES` 配列にファイル名を追加する

3. `install-claude.ps1` の `$files` 配列にファイル名を追加する（シングルクォート + カンマ。末尾要素のカンマの有無に注意）

4. 検証する。

   ```bash
   cd ~/dotfiles
   uv run pyfltr run-for-agent  # 全テスト green 必須
   ```

## 削除手順

1. 配布元ファイルを削除する — `.chezmoi-source/dot_claude/rules/agent-toolkit/<rule>.md`

2. `install-claude.sh` の `FILES` 配列から除去する

3. `install-claude.ps1` の `$files` 配列から除去する

4. 検証する（追加手順と同じ）

既存環境の配布先ファイルは、`install-claude.sh` / `.ps1` の再実行時にステージング差し替え方式で自動的に消える。
chezmoi経由の環境でも `claudize` 経由の環境でも同様に、配布先ディレクトリが配布元と完全一致するよう再配置されるため追加の削除追跡は不要。

## 避けるべき失敗

- `.chezmoi-source/dot_claude/rules/agent-toolkit/` 以外の場所に配布元を配置しない
- `install-claude.sh` と `install-claude.ps1` は必ず同時に更新する（片方のみでは配布経路の一方が不整合になる）
- 新しい無条件ルールを追加する際、コンテキスト消費が大きくなる点に注意する

## 参考

- 配布方式の詳細: [docs/guide/claude-code.md](../../../docs/guide/claude-code.md)
- ルール・スキルなどの記述ガイドライン: [plugins/agent-toolkit/skills/claude-meta-rules/SKILL.md](../../../plugins/agent-toolkit/skills/claude-meta-rules/SKILL.md)
