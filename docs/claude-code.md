# Claude Code 設定管理

本リポジトリの Claude Code 関連ファイルは、他のプロジェクトで利用するためのテンプレートも兼ねている。
配布元は 2 系統ある。

- ルール: `.chezmoi-source/dot_claude/rules/agent-basics/` 配下のファイル群
- plugin: `plugins/` 配下の Claude Code plugin (現在は `edit-guardrails` のみ)

ルール群は他のユーザーと共有される可能性があるため、個別ツール (`claudize` など) への依存を含めないようにしている。
plugin 群は `.claude-plugin/marketplace.json` 経由で配布する。

配布元の管理やツール固有の情報はこの文書に集約する。

## 配布方式

### 自分用: dotfiles 経由 (chezmoi)

本 dotfiles を導入している環境では、chezmoi が配布元ディレクトリを `~/.claude/rules/agent-basics/` へデプロイする。
配置されたルールは全プロジェクトで自動読み込みされ、プロジェクトごとの個別配布は不要。

更新: `update-dotfiles` (または `chezmoi apply`) で最新化される。

### 他人に配布: `install-claude.sh` / `install-claude.ps1`

チームメンバーにルールを使ってもらいたい場合向けのワンライナーインストーラー。
dotfiles 全体を入れずに `~/.claude/rules/agent-basics/` だけを配置できる。

想定用途はチームプロジェクトのセットアップ手順への組み込み。
各メンバーはインストール後、必要に応じて `paths` frontmatter などを編集してプロジェクト事情に合わせてカスタマイズできる。
再実行時は既存ファイルの frontmatter を維持したまま body のみ更新されるため、frontmatter のカスタマイズは保持される (body の変更は破棄される点に注意)。
配布元の agent.md のように frontmatter を持たないファイルでも、ローカルで frontmatter を追記していれば維持される。

差分が発生した場合のバックアップは `~/.claude/rules-backup/agent-basics-<timestamp>/` に作成される。
Claude Code が `~/.claude/rules/` 配下を再帰的に読み込む仕様のため、退避ファイルが読まれないよう `rules/` の外に逃がしている。

#### プロジェクトのセットアップ手順への記述例

````markdown
## Claude Code ルールのインストール

本プロジェクトでは、共通の Claude Code ルールを `~/.claude/rules/agent-basics/` に配置することを推奨する。

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
```

### Windows (cmd)

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
```

### ルールの更新・カスタマイズ

ルールファイルは頻繁に更新される可能性があるため、定期的にインストールコマンドを再実行して最新化すること。

インストール後、`~/.claude/rules/agent-basics/` 配下のファイルを必要に応じて編集できる。
たとえば `paths` frontmatter を変更すれば、各ルールの適用範囲を限定できる。
上記のワンライナーを再実行した場合、既存ファイルの frontmatter は維持され body のみ更新される。

frontmatter 以外 (body 部分) を編集すると、再実行時に上書きされて変更が破棄される。
body をカスタマイズしたい場合は、`paths` frontmatter に存在しない拡張子 (例: `paths: ["**/*.__disabled__"]`) を指定して該当ルールを実質無効化したうえで、
別ファイルとして独自ルールを管理する方法を推奨する。

body に差分があった場合、旧ファイルは `~/.claude/rules-backup/agent-basics-<timestamp>/` に退避される。
(バックアップ先を `~/.claude/rules/` の外に置いているのは、Claude Code が `~/.claude/rules/` 配下を再帰的に読み込む仕様のためである。
退避先が同じツリー内にあると古いルールが読まれてしまう。)
不要になったバックアップは適宜削除する。

## edit-guardrails プラグインのインストール

本プロジェクトでは、危険な編集を PreToolUse 段階で検出する Claude Code プラグイン `edit-guardrails` も推奨する。
現在のチェック内容は次の通り。

- 文字化け (U+FFFD) を含む Write/Edit/MultiEdit をブロック
- LF 改行のみの `.ps1` / `.ps1.tmpl` への書き込みをブロック (Windows PowerShell 5.1 対策)

```bash
claude plugin marketplace add ak110/dotfiles
claude plugin install edit-guardrails@ak110-dotfiles
```

インストール済みかを確認するには `claude plugin list` を使う。
削除には `claude plugin uninstall edit-guardrails` を使う。
プラグインは `uv` CLI に依存するため、別途 [uv](https://docs.astral.sh/uv/) をインストールすること。

### プラグインの更新

ローカルの `marketplace.json` にある version 情報を最新化してから `plugin update` を実行する。
`marketplace update` を省くと古いメタデータが使われ `既に最新です` と誤判定されることがある。

```bash
claude plugin marketplace update ak110-dotfiles
claude plugin update edit-guardrails@ak110-dotfiles
```

dotfiles を `install.sh` / `install.ps1` 経由でフル導入している場合、
`update-dotfiles` が `chezmoi apply` の後処理として上記と同等のコマンドを自動実行する
(`pytools/_install_claude_plugins.py`)。
明示的に `claude plugin update` を実行する必要があるのは、dotfiles を使っていない環境や、
`update-dotfiles` を経由せずにプラグインだけ最新化したい場合である。
````

### プロジェクトローカルへの配布: `claudize`

プロジェクトの `.claude/rules/agent-basics/` にルールファイルを直接置きたい場合向けに `claudize` コマンドを残している。
例: 他者と作業するプロジェクトで、リポジトリ内に特定の言語ルールだけを共有したい場合。

```bash
# プロジェクトディレクトリで実行 (配布)
cd ~/your-project
claudize

# 解除 (配布済みファイルを削除)
claudize --clean
```

`claudize` は対象プロジェクトに該当言語のファイルが存在する場合のみ言語別ルールを配布する。

### Claude Code plugin (edit-guardrails)

`plugins/edit-guardrails/` に、危険な編集を PreToolUse 段階で検出する plugin を同梱している。
配布元は本リポジトリ自身で、`.claude-plugin/marketplace.json` に登録済み。
`uv` CLI に依存する (hook スクリプトを `uv run --script` 経由で実行するため)。

主なチェック内容は次の通り。

- 文字化け (U+FFFD) を含む Write/Edit/MultiEdit をブロック
- LF 改行のみの `.ps1` / `.ps1.tmpl` への書き込みをブロック (Windows PowerShell 5.1 対策)
- ロックファイルや `.venv/` / `node_modules/` など自動生成物の手編集をブロック
- シークレットらしき値の書き込みや、`~` 展開パスのハードコードを警告・ブロック

最新の一覧と詳細は `plugins/edit-guardrails/scripts/pretooluse.py` を参照。

個人環境 (dotfiles 導入済み) では `chezmoi apply` 後処理で自動インストールされる
(`claude` と `uv` が PATH にあり未導入の場合のみ)。
手動インストールや他のチームメンバー向けの配布方法は上記「プロジェクトのセットアップ手順への記述例」を参照。

## ルールファイル一覧

- `agent.md`: 自動化すべき部分とユーザー確認すべき部分のバランス調整、コード品質の維持のためのルール (無条件ロード)
- `{言語}.md`: 言語固有のコーディング規約 (`paths` frontmatter で該当言語ファイル編集時のみロード)
- `{言語}-test.md`: 言語固有のテスト方針 (同上)
- `markdown.md`: Markdown 記述スタイル (`.md`/`.mdx` 編集時のみロード)
- `rules.md`: ルール作成時のガイドライン (`.claude/rules/` 編集時のみロード)
- `skills.md`: スキル作成時のガイドライン (`.claude/skills/` 編集時のみロード)

`agent.md` 以外は `paths` frontmatter で該当拡張子のファイルを読んだときのみロードされる。
セッション開始時のコンテキスト消費を抑えるための仕組みであり、プロジェクト単位の厳密な分離ではない (たとえば Python ファイルを編集すれば `python.md` はロードされる)。

CLAUDE.md はプロジェクト固有の情報を記述するファイルとして、ルール配布の管理対象外。
プロジェクトごとに手動で管理する (`/init` コマンドなどを活用するのも手)。

## 配布対象ファイル一覧の SSOT

ファイル一覧は以下3箇所で重複管理している。

- `pytools/claudize.py` — `_UNCONDITIONAL_RULES` / `_CONDITIONAL_RULES`
- `install-claude.sh` — `FILES` 配列
- `install-claude.ps1` — `$files` 配列

新しいルールファイルを追加する際は、これら3箇所すべてを更新すること。
