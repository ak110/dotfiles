---
name: sync-rule-ssot
description: dotfilesリポジトリでagent-basicsルールファイルを追加・削除・リネームする際に必ず使う。配布対象ファイル一覧が4箇所（`pytools/claudize.py`・`install-claude.sh`・`install-claude.ps1`・`pytools/post_apply.py`）に重複しているため、1つだけ更新して他を忘れると配布に失敗する。「ルールを追加」「ルールを削除」「`rules/agent-basics`を変更」などのキーワードで自動トリガーしてよい
user-invocable: false
---

# agent-basics ルールの SSOT 同期

## 目的

`~/dotfiles/` リポジトリのagent-basicsルール配布は、配布対象のファイル一覧を以下の4箇所で重複管理している。

- `pytools/claudize.py` の `_UNCONDITIONAL_RULES` / `_CONDITIONAL_RULES` / `_OBSOLETE_RULES`
- `install-claude.sh` の `FILES` 配列 / `OBSOLETE_FILES` 配列
- `install-claude.ps1` の `$files` 配列 / `$obsoleteFiles` 配列
- `pytools/post_apply.py` の `_REMOVED_PATHS`（chezmoi apply後処理で配布先から削除するパス一覧）

新規追加・削除・リネーム時にこの4箇所の同期を怠ると、配布に失敗する。
プロジェクトローカル配布（claudize）、リモートインストーラー経由の配布（install-claude.sh/.ps1）、およびchezmoi経由の配布先クリーンアップのいずれかが機能しなくなる。
このスキルは追加・削除・リネーム手順を標準化する。

## 前提確認

作業を始める前に、編集するリポジトリが `~/dotfiles`（または同等の本家dotfilesチェックアウト）であるか確認する。他プロジェクトの `.claude/rules/` は配布先であり、原本ではない。

原本ディレクトリ: `.chezmoi-source/dot_claude/rules/agent-basics/`

## ルールの種別

新規ルールは以下のどちらかに分類する。

- 無条件ルール: セッション開始時に常に読み込まれる。`paths` frontmatterを持たない
  - 配布対象は `pytools/claudize.py` の `_UNCONDITIONAL_RULES` に追加する
  - `agent.md` は特別扱いで `claudize.py` の既定配布リストに直接記載されているため、`_UNCONDITIONAL_RULES` への追加は不要
- 条件付きルール（拡張子別）: 特定の拡張子のファイルを編集したときだけ読み込まれる。`paths` frontmatterで対象を絞る
  - `_CONDITIONAL_RULES` に `(ファイル名, (拡張子, ...))` のタプルで追加する

ルール本体（`*.md`）には以下のfrontmatterを付ける。

```yaml
---
paths:
  - "**/*.py" # 条件付きルールの場合のみ
---
```

`paths` を省略すると無条件ルールになる。

## 追加手順

1. 原本ファイルを作成する — `.chezmoi-source/dot_claude/rules/agent-basics/<new-rule>.md` を追加する。frontmatterは上記に従う

2. `pytools/claudize.py` を更新する — 以下のどちらかに追加する。

   ```python
   _UNCONDITIONAL_RULES: list[str] = ["markdown.md", "<new-rule>.md"]
   # または
   _CONDITIONAL_RULES: list[tuple[str, tuple[str, ...]]] = [
       ("<new-rule>.md", ("<.ext1>", "<.ext2>")),
   ]
   ```

3. `install-claude.sh` の `FILES` 配列に追加する — コメント（`pytools/claudize.py の ... と一致させること`）は保持する

4. `install-claude.ps1` の `$files` 配列に追加する — シングルクォート + カンマで囲む文字列リテラル。末尾要素のカンマの有無に注意する

5. 検証する。

   ```bash
   cd ~/dotfiles
   uv run pyfltr run-for-agent  # 全テスト green 必須
   # 原本ディレクトリから実際に読めるかを claudize の dry-run で確認
   claudize --help
   ```

6. 動作確認する（任意だが推奨）— 本家dotfiles以外の作業ツリーで `claudize` を実行してルールが配布されることを確認する

## 削除手順

1. 原本ファイルを削除する — `.chezmoi-source/dot_claude/rules/agent-basics/<rule>.md` を削除する

2. `pytools/claudize.py` の該当エントリを `_UNCONDITIONAL_RULES` または `_CONDITIONAL_RULES` から除去し、`_OBSOLETE_RULES` に追加する

3. `install-claude.sh` の `FILES` から除去し、`OBSOLETE_FILES` に追加する

4. `install-claude.ps1` の `$files` から除去し、`$obsoleteFiles` に追加する

5. `pytools/post_apply.py` の `_REMOVED_PATHS` に `Path("rules/agent-basics/<rule>.md")` を追加する。これにより `chezmoi apply` 経由で配布された環境のファイルもクリーンアップされる

6. 検証手順は追加手順と同じ

## リネーム手順

旧名の削除と新名の追加を同じ手順で実施する。`pytools/claudize.py` の `targets` リストも再確認し、漏れがないか検証する。

## 避けるべき失敗

- `.chezmoi-source/dot_claude/rules/agent-basics/` 以外の場所に原本を配置しない
- `install-claude.sh` と `install-claude.ps1` は必ず同時に更新する（片方のみの更新では配布経路の一方が機能しなくなる）
- `_UNCONDITIONAL_RULES` と `_CONDITIONAL_RULES` の両方に同じファイル名を記載しない
- 新しい無条件ルールを追加する際、コンテキスト消費が大きくなる点に注意する。可能な限り `paths` で条件付きにする
- 削除時に `_OBSOLETE_RULES` / `OBSOLETE_FILES` / `$obsoleteFiles` / `_REMOVED_PATHS` のどれかへの登録を忘れると、既存環境からの古いファイル削除が行われず、新旧ルールの混在状態が残る

## 参考

- 配布方式の詳細: `docs/guide/claude-code.md`
- ルール・スキルなどの記述ガイドライン: `plugins/agent-toolkit/skills/claude-meta-rules/SKILL.md`
