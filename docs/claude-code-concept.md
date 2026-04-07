# Claude Code 共有ルール / プラグイン

[ak110/dotfiles](https://github.com/ak110/dotfiles) では Claude Code 向けに以下の 2 種類の共有設定を提供している。

- 共有ルール (`~/.claude/rules/agent-basics/` 配下) — 全プロジェクトで自動読み込みされるコーディング規約・運用方針
- `edit-guardrails` プラグイン — 危険な編集を `PreToolUse` 段階で検出する Claude Code plugin

実際のインストール手順は元リポジトリの [docs/claude-code.md](claude-code.md) を参照。
ここでは「中身」と「カスタマイズの仕方」を説明する。

## 共有ルール (`agent-basics`)

### 概要

`~/.claude/rules/agent-basics/` 配下に以下のファイルが配置される。

- `agent.md`: 自動化すべき部分とユーザー確認すべき部分のバランス調整、コード品質の維持のためのルール (無条件ロード)
- `{言語}.md`: 言語固有のコーディング規約 (`paths` frontmatter で該当言語ファイル編集時のみロード)
- `{言語}-test.md`: 言語固有のテスト方針 (同上)
- `markdown.md`: Markdown 記述スタイル (`.md` / `.mdx` 編集時のみロード)
- `rules.md`: ルール作成時のガイドライン (`.claude/rules/` 編集時のみロード)
- `skills.md`: スキル作成時のガイドライン (`.claude/skills/` 編集時のみロード)

`agent.md` 以外は `paths` frontmatter で該当拡張子のファイルを読んだときのみロードされる。
セッション開始時のコンテキスト消費を抑えるための仕組みであり、プロジェクト単位の厳密な分離ではない (たとえば Python ファイルを編集すれば `python.md` はロードされる)。

`CLAUDE.md` はプロジェクト固有の情報を記述するファイルとして、配布の管理対象外。
プロジェクトごとに手動で管理する (`/init` コマンドなどを活用するのも手)。

### 更新

ルールファイルは頻繁に更新される可能性があるため、定期的にインストールコマンドを再実行して最新化することを推奨する。
再実行時は既存ファイルの frontmatter を維持したまま body のみ更新される。

### カスタマイズ

インストール後、`~/.claude/rules/agent-basics/` 配下のファイルは必要に応じて編集できる。
たとえば `paths` frontmatter を変更すれば、各ルールの適用範囲を限定できる。
配布元の `agent.md` のように frontmatter を持たないファイルでも、ローカルで frontmatter を追記していれば再実行時に維持される。

frontmatter 以外 (body 部分) を編集すると、再実行時に上書きされて変更が破棄されてしまう。
body をカスタマイズしたい場合は、`paths` frontmatter に存在しない拡張子を指定して該当ルールを実質無効化する。
たとえば `paths: ["**/*.__disabled__"]` のように設定したうえで、別ファイルとして独自ルールを管理するのを推奨する。

### バックアップ

body に差分があった場合、旧ファイルは `~/.claude/rules-backup/agent-basics-<timestamp>/` に退避される。
バックアップ先を `~/.claude/rules/` の外に置いているのは、退避先が同じツリー内にあると古いルールも再帰的に読まれてしまうため。
不要になったバックアップは適宜削除する。

## edit-guardrails プラグイン

好ましくない編集を `PreToolUse` 段階で検出してブロックする Claude Code プラグイン。
Marketplace `ak110-dotfiles` から配布している。

### 主なチェック内容

- 文字化け (U+FFFD) を含む `Write` / `Edit` / `MultiEdit` をブロック
- LF 改行のみの `.ps1` / `.ps1.tmpl` への書き込みをブロック (Windows PowerShell 5.1 対策)
- ロックファイルや `.venv/` / `node_modules/` など自動生成物の手編集をブロック
- シークレットらしき値の書き込みや、ホームディレクトリの絶対パスのハードコードを警告・ブロック

### 依存

プラグインは [uv](https://docs.astral.sh/uv/) に依存する (hook スクリプトを `uv run --script` 経由で実行するため)。
事前にインストールしておくこと。

### プラグインの更新

```bash
claude plugin marketplace update ak110-dotfiles && claude plugin update edit-guardrails@ak110-dotfiles
```

### 削除

```bash
claude plugin uninstall edit-guardrails
```

インストール済みかは `claude plugin list` で確認できる。
