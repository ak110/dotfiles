---
name: sync-rule-ssot
description: >
  dotfilesリポジトリでagent-toolkitルールファイル（`agent-toolkit/rules/`配下）を
  追加・削除・リネームする際に必ず使う。
  「ルールを追加」「ルールを削除」「`rules/agent-toolkit`を変更」などのキーワードで自動トリガーしてよい。
user-invocable: false
---

# agent-toolkit ルールの SSOT 同期

配布元ディレクトリ `agent-toolkit/rules/` がSSOT。
chezmoi経由の配布と `claudize` コマンドはディレクトリ全体を同期するため、
ファイルの追加・削除・リネーム時にそれら経路のコード修正は不要。

`install-claude.sh` / `install-claude.ps1` はリモートインストーラー（`curl ... | bash`のワンライナー実行を想定）で、
配布経路を介さずGitHub Raw経由で個別ファイルを取得する。
依存を減らすためリポジトリを丸ごとcloneせず、配布対象のファイル名を配列としてスクリプト内に持つ構造のため、
配布対象ファイルの増減に合わせて手動で更新する。

## 前提

編集するリポジトリが `~/dotfiles`（または同等の本家dotfilesチェックアウト）であることを確認する。
他プロジェクトの `.claude/rules/` は配布先であり原本ではない。
他プロジェクトで`.claude/rules/`配下を編集しても、次回の配布で上書きされるため恒久化しない。

配布元ディレクトリ: `agent-toolkit/rules/`

## 追加・リネーム手順

1. 配布元ファイルを作成（またはリネーム）する — `agent-toolkit/rules/<rule>.md`

2. `install-claude.sh` の `FILES` 配列にファイル名を追加する

3. `install-claude.ps1` の `$files` 配列にファイル名を追加する（シングルクォート + カンマ。末尾要素のカンマの有無に注意）

4. 検証する。

   ```bash
   cd ~/dotfiles
   uvx pyfltr run-for-agent  # 全テスト green 必須
   ```

## 削除手順

1. 配布元ファイルを削除する — `agent-toolkit/rules/<rule>.md`

2. `install-claude.sh` の `FILES` 配列から除去する

3. `install-claude.ps1` の `$files` 配列から除去する

4. 検証する（追加手順と同じ）

既存環境の配布先ファイルは、`install-claude.sh` / `.ps1` の再実行時にステージング差し替え方式で自動的に消える。
chezmoi経由の環境でも `claudize` 経由の環境でも同様に、配布先ディレクトリが配布元と完全一致するよう
再配置されるため追加の削除追跡は不要。

## 避けるべき失敗

- `agent-toolkit/rules/` 以外の場所に配布元を配置しない
- `install-claude.sh` と `install-claude.ps1` は必ず同時に更新する（片方のみでは配布経路の一方が不整合になる）
- 新しい無条件ルールを追加する際、コンテキスト消費が大きくなる点に注意する

## 参考

- 配布方式の詳細: [CLAUDE.md](../../../CLAUDE.md) と
  [.claude/rules/agent-toolkit.md](../../rules/agent-toolkit.md)
- ルール・スキルなどの記述ガイドライン:
  [agent-toolkit/skills/writing-standards/SKILL.md](../../../agent-toolkit/skills/writing-standards/SKILL.md)
