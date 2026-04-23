# CLAUDE.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリ。
`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## 主なディレクトリ

- `.chezmoi-source/` — chezmoiソースディレクトリ（`dot_` prefix → `~/.*`に反映される）
- `pytools/` — Pythonコマンドラインツール群（uv tool installでインストール）
- `bin/` — ユーザーのPATHに追加して使うコマンドラッパー（リポジトリ直下でgit管理）
- `plugins/` — Claude Code用プラグイン（マーケットプレイス経由で他人にも配布）
- `scripts/` — リポジトリ開発専用スクリプト（pre-commit/Makefileから呼ばれる。配布対象外）
- `.claude/` — dotfilesリポ自身のClaude Codeプロジェクト設定（配布対象外）

## 開発手順

- `make update`: 依存更新 + pre-commit autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）
- コミット前の検証方法: `uv run pyfltr run-for-agent`
  - ドキュメントなどのみの変更の場合は省略可（pre-commitで実行されるため）
  - テストコードの単体実行なども極力 `uv run pyfltr run-for-agent <path>` を使う（pytestを直接呼び出さない）
    - 詳細な情報などが必要な場合に限り `uv run pytest -vv <path>` などを使用
  - 修正後の再実行時は、対象ファイルや対象ツールを必要に応じて絞って実行する（最終検証はCIに委ねる前提）
    - 例: `pyfltr run-for-agent --commands=mypy,ruff-check path/to/file`

## chezmoiの命名規則（早見表）

`.chezmoi-source/`配下のファイル名は以下の規則で`~/`配下にデプロイされる（詳細はchezmoi公式: <https://www.chezmoi.io/reference/source-state-attributes/>）。

- `dot_<name>` → `~/.<name>`（例: `dot_bashrc` → `~/.bashrc`）
- `private_<name>` → パーミッション`600`／ディレクトリは`700`で配置
- `executable_<name>` → 実行権限（`+x`）付きで配置
- `<name>.tmpl` → Goテンプレートとして評価してから配置
- `run_onchange_after_<name>.sh.tmpl` → `chezmoi apply`時に変更検知して実行
- よく使うコマンド: `chezmoi apply`（反映）・`chezmoi diff`（差分確認）・`chezmoi managed | grep <相対パス>`（配布対象確認）

## 注意点

- `.claude`を含むディレクトリが3系統あり取り違えやすい（`.chezmoi-source/dot_claude/` / `~/.claude/` / `.claude/`）。
  指示の対象を必ず確認する。詳細は[docs/development/development.md](docs/development/development.md)の
 「ディレクトリ構造の注意」参照
- ホーム配下のファイルを編集する前に`chezmoi managed | grep <相対パス>`で配布対象か確認する。
  配布対象は`.chezmoi-source/`側を編集する
- `.chezmoi-source/`配下のファイルを削除した場合、chezmoiは配布先を自動削除しない。
  配布先から除去するには`pytools/post_apply.py`の`_REMOVED_PATHS`に対象パスを追記する（`chezmoi apply`後処理で削除される）
- 配布対象（Linux/Windows両対応）と開発対象（Linuxのみ）でサポート範囲が異なる。ファイル追加時にどちら用か意識する
- プラットフォーム対応ファイル（Linux/Windowsのペア）は一方を変更したらもう一方も確認する。
  対応ファイル一覧は[docs/development/development.md](docs/development/development.md)の「プラットフォーム対応ファイル」参照
- リポジトリ内リソースを参照するスクリプトは`Path.home()`起点ではなく`Path(__file__)`起点で解決する。
  CIチェックアウトや利用者環境で`$HOME`と`~/dotfiles`が一致しない場合にimportが壊れるため
- シンプルなコマンドラッパーの新規追加には`scripts/new-bin-cmd.py <name> <command...>`を使う。
  リポジトリ直下の`bin/<name>`と`bin/<name>.cmd`のペアを生成し、`development.md`のペア一覧も自動更新する
- `agent-toolkit/*.md`（配布ルール本体）を改訂する際、`docs/guide/claude-code-guide.md`に要約・ステップ数などが
  再掲されていることが多い。本体変更前に`grep`で参照箇所を確認する
- `agent-toolkit/`配下のファイル分割（`agent.md`・`styles.md`など）は編集・レビュー時の見通し改善が目的で、
  配布先の`~/.claude/rules/agent-toolkit/`では全ファイルが常時自動ロードされる
- `agent-toolkit/agent.md`のコミットメッセージ方針と`.gitmessage`は意図的に重複させている。
  前者はプラグイン配布対象（他リポジトリでも参照される）、後者は本リポジトリ固有のコミット補助テンプレート
 （`claude-commit`等も参照する想定）のため、SSOT化せず双方に必要な情報を持たせる。片方を参照リンクに置き換えない
- spec-driven系スキル（`spec-driven`・`spec-driven-init`・`spec-driven-promote`）は本リポジトリでは対象外。
  `docs/features/`・`docs/topics/`の運用を採らないため、機能追加時も起動しない
- `pytools/`トップレベルには`project.scripts`から参照される公開CLIモジュールを置く。
  privateなヘルパー（chezmoi運用補助・共通ユーティリティなど）は`pytools/_internal/`配下に集約する
- 依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使う。`UV_FROZEN`はCI/make内で自動適用される
- ルート直下の`configuration.dsc.yaml`はWindows向けレジストリ設定を
  `winget configure`で宣言的に適用するDSCファイル（`post_apply`のステップから呼ぶ）
