# claude-commit 設計ドキュメント

## 概要

ステージング済みの変更をclaudeで分析してコミットメッセージを生成し、claudeがgit commitを実行するCLIコマンド。
claudeにBashツールを渡すことで、変更が複数の論理単位にまたがる場合のコミット分割にも対応する。

## 要件

- claudeコマンドを呼び出してステージング済み変更を分析し、適切なコミットを実行させる
- claudeはBashツールを持ち、git commitを直接実行する（複数コミットへの分割も可能）
- `--amend`時はステージング済み変更＋HEADコミットの内容を両方参照してamendする
- フォーマット: `git config commit.template` → `.gitmessage`（リポジトリルート）→ Conventional Commits（日本語）の優先順で決定
- CLAUDE.mdは読まない（`--bare`）
- デフォルトモデルはsonnet、引数で変更可能

## コマンドIF

```text
claude-commit [OPTIONS]
```

### オプション

| オプション | デフォルト | 説明 |
| --- | --- | --- |
| `--amend` | off | HEADのコミットをamend |
| `--edit` / `-e` | off | エディターで編集してからコミット |
| `--dry-run` | off | 生成メッセージの表示のみ（コミットしない） |
| `--model` / `-m` | sonnet | claudeのモデル |
| `--effort` | なし | 思考レベル（low/medium/high/max） |

## 内部設計

### フロー

1. ステージング済み変更の確認
   - `git diff --cached`（除外パターン適用後）が空なら終了（`--amend`時はHEAD差分が無くてもOK）
2. フォーマット決定
   - リポジトリルートの`.gitmessage`を最優先で確認（リポジトリ固有のため）
   - 存在しない場合は`git config commit.template`で設定されたファイルを確認
   - いずれも存在しない場合はConventional Commits（日本語）をデフォルトとして使用
3. プロンプト構築
   - 通常: ステージング済み差分 + フォーマット指示 + git commit実行指示
   - `--amend`: HEADメッセージ + HEAD差分 + ステージング済み差分 + amend指示
4. claude呼び出し（非インタラクティブ、Bashツール付き）
5. claudeがgit commitを実行（コミット分割も判断可能）

### 差分除外パターン

コンテキスト長節約のため、以下のパターンに一致するファイルを全差分取得から除外する。

```python
EXCLUDE_PATTERNS = [
    "*.lock",            # uv.lock, poetry.lock, yarn.lock, Cargo.lock, Gemfile.lock 等
    "package-lock.json",
    "pnpm-lock.yaml",
]
```

`git diff`コマンドに `-- ':!*.lock' ':!package-lock.json' ':!pnpm-lock.yaml'` の形式でpathspec除外を渡す。

### claude呼び出し

```text
claude --print --bare --tools "Bash" --permission-mode bypassPermissions \
  --model <model> --no-session-persistence [--effort <level>] "<prompt>"
```

- `--print`: 非インタラクティブ出力
- `--bare`: CLAUDE.md/フック/LSPなどをスキップ
- `--tools "Bash"`: git操作のためBashツールのみ有効化
- `--permission-mode bypassPermissions`: スクリプト実行中の確認ダイアログをスキップ
- `--no-session-persistence`: 使い捨てセッション

### プロンプト（通常）

```text
以下のgit差分を分析して、適切なgit commitを実行してください。

変更が明確に複数の論理単位にまたがる場合は複数のコミットに分割してください。
分割する場合は git restore --staged / git add を使って適切にステージングを組み替えてください。

# フォーマット
{フォーマット指示}

# ステージング済みの変更
{git diff --cached の出力}
```

`--dry-run`時:「実際にコミットはしないでください。実行するコミットメッセージを表示するだけにしてください」を追加。
`--edit`時:「コミット時は `git commit -e` を使ってください」を追加。

### プロンプト（--amend）

```text
以下の情報を元に、HEADのコミットをgit commit --amendで改訂してください。

# フォーマット
{フォーマット指示}

# 既存のコミットメッセージ
{git log -1 --format=%B の出力}

# 既存のコミット差分（HEAD）
{git show HEAD の出力}

# 追加のステージング済み変更
{git diff --cached の出力}（空の場合は省略）
```

`--edit`時:「コミット時は `git commit --amend -e` を使ってください」を追加。

## ファイル構成

- `pytools/claude_commit.py` — コマンド本体
- `tests/claude_commit_test.py` — テスト
- `pyproject.toml` — エントリポイント追加（`claude-commit`）
- `docs/guide/pytools.md` — コマンド追加（あれば）
