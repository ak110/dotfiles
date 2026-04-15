---
name: sync-rule-ssot
description: dotfiles リポジトリで新しい agent-basics ルールファイルを追加・削除・リネームする際に必ず使う。配布対象ファイル一覧が 3 箇所 (pytools/claudize.py、install-claude.sh、install-claude.ps1) に重複しているため、1 つだけ更新して他を忘れると配布に失敗する。「ルールを追加」「ルールを削除」「rules/agent-basics を変更」などのキーワードで自動トリガーしてよい
user-invocable: false
---

# agent-basics ルールの SSOT 同期

## 目的

`~/dotfiles/` リポジトリのagent-basicsルール配布は、配布対象のファイル一覧を以下の3箇所で重複管理している。

- `pytools/claudize.py` の `_UNCONDITIONAL_RULES` / `_CONDITIONAL_RULES`
- `install-claude.sh` の `FILES` 配列
- `install-claude.ps1` の `$files` 配列

新規追加・削除・リネーム時にこの3箇所の同期を怠ると、配布に失敗する。
プロジェクト ローカル配布（claudize）またはリモート インストーラー経由の配布 (install-claude.sh/.ps1) のどちらかが機能しなくなる。
このスキルは追加・削除・リネーム手順を標準化する。

## 前提確認

作業を始める前に、編集するリポジトリが `~/dotfiles`（または同等の本家dotfilesチェックアウト）であるか確認する。他プロジェクトの `.claude/rules/` は配布先であり、原本ではない。

原本ディレクトリ: `.chezmoi-source/dot_claude/rules/agent-basics/`

## ルールの種別

新規ルールは以下のどちらかに分類する。

- 無条件ルール: セッション開始時に常に読み込まれる。`paths` frontmatterを持たない。
  - 例: `agent.md`, `claude.md`, `claude-rules.md` など
  - `agent.md` は特別扱いで、`claudize.py` の既定配布リストに直接記載されている（配布対象一覧に加える必要はない）
  - それ以外の無条件ルールは `_UNCONDITIONAL_RULES` に追加する
- 条件付きルール（言語別）: 特定の拡張子のファイルを編集したときだけ読み込まれる。`paths` frontmatterで対象を絞る。
  - 例: `python.md` / `python-test.md` (`.py`)、`typescript.md` / `typescript-test.md` (`.ts`, `.tsx`)
  - `_CONDITIONAL_RULES` に `(ファイル名, (拡張子, ...))` のタプルで追加する

ルール本体 (`*.md`) には以下のfrontmatterを付ける。

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
   _UNCONDITIONAL_RULES: list[str] = ["claude.md", "claude-rules.md", "claude-skills.md", "markdown.md", "<new-rule>.md"]
   # または
   _CONDITIONAL_RULES: list[tuple[str, tuple[str, ...]]] = [
       ("python.md", (".py",)),
       ...,
       ("<new-rule>.md", ("<.ext1>", "<.ext2>")),
   ]
   ```

3. `install-claude.sh` の `FILES` 配列に追加する — コメント (`pytools/claudize.py の ... と一致させること`) は保持する

4. `install-claude.ps1` の `$files` 配列に追加する — シングル クォート + カンマで囲む文字列リテラル。末尾要素のカンマの有無に注意する

5. 検証する。

   ```bash
   cd ~/dotfiles
   uv run  pyfltr run-for-agent  # 全テスト green 必須
   # 原本ディレクトリから実際に読めるかを claudize の dry-run で確認
   claudize --help
   ```

6. 動作確認する（任意だが推奨）— 本家dotfiles以外の作業ツリーで `claudize` を実行してルールが配布されることを確認する

## 削除手順

追加の逆順で4箇所 (原本 `.md` + 3つのSSOT) から削除する。`claudize --clean` が過去に配布したファイルを回収できるよう、削除前の段階でリネーム扱いにできないか検討する。

## リネーム手順

旧名の削除と新名の追加を同じ手順で実施する。`pytools/claudize.py` の `targets` リストも再確認し、漏れがないか検証する。

## 避けるべき失敗

- `.chezmoi-source/dot_claude/rules/agent-basics/` 以外の場所に原本を配置しない
- `install-claude.sh` と `install-claude.ps1` は必ず同時に更新する（片方のみの更新では配布経路の一方が機能しなくなる）
- `_UNCONDITIONAL_RULES` と `_CONDITIONAL_RULES` の両方に同じファイル名を記載しない
- 新しい無条件ルールを追加する際、コンテキスト消費が大きくなる点に注意する。可能な限り `paths` で条件付きにする

## 参考

- 配布方式の詳細: `docs/guide/claude-code.md`
- 原本ルールの書き方: `.chezmoi-source/dot_claude/rules/agent-basics/claude-rules.md`
