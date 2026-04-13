# カスタム指示（プロジェクト固有）

## プロジェクト概要

chezmoi管理のdotfilesリポジトリ。`.chezmoi-source/`配下を`~/.*`にデプロイする。
配布対象のルール・プラグインはClaude Code用の共有設定を兼ねる。

## 開発手順

- `make format`: 整形 + 軽量lint + 自動修正（開発時の手動実行用）
- `make test`: 全チェック実行（これを通過すればコミット可能）
- `make update`: 依存更新 + pre-commit autoupdate + pinactアクション更新 + 全テスト実行
  - `make update-actions`: GitHub Actionsのハッシュピン更新のみ（mise経由でpinact実行）

- ドキュメントのみの変更（`*.md`や`docs/**`の更新）をコミットする場合、事前の手動`make test`は省略してよい。`git commit`時点で`pre-commit`の`pyfltr fast`フックが`markdownlint-fast`と`textlint-fast`を自動実行するため、Markdownの検証はそこで担保される
- コードやテストに手を入れた変更では従来どおり`make test`を通してからコミットする

## Claude Code向けコミット前検証

Claude Codeがコミット前に検証する際は、`make test`の代わりに以下を実行する。JSON Lines出力によりLLMがツール別診断を効率的に解釈できる。

```bash
uv run pyfltr run --output-format=jsonl
```

人間の開発者は従来どおり`make test`を使用する。

## ディレクトリ構造の要点

- `.chezmoi-source/` — chezmoiソースディレクトリ（配布対象。`dot_` prefix → `~/.*`）
- `pytools/` — Pythonコマンドラインツール群（uv tool installでインストール）
- `plugins/` — Claude Code用プラグイン（Marketplace経由で他人にも配布）
- `scripts/` — リポジトリ開発専用スクリプト（pre-commit/Makefileから呼ばれる。配布対象外）
- `.claude/` — dotfilesリポ自身のClaude Codeプロジェクト設定（配布対象外）

## 重大な注意点

- `.claude`を含むディレクトリが3系統あり取り違えやすい（`.chezmoi-source/dot_claude/` / `~/.claude/` / `.claude/`）。指示の対象を必ず確認する。詳細は[docs/development/development.md](docs/development/development.md)の「ディレクトリ構造の注意」参照
- ホーム配下のファイルを編集する前に`chezmoi managed | grep <相対パス>`で配布対象か確認する。配布対象は`.chezmoi-source/`側を編集する
- 配布対象（Linux/Windows両対応）と開発対象（Linuxのみ）でサポート範囲が異なる。ファイル追加時にどちら用か意識する
- プラットフォーム対応ファイル（Linux/Windowsのペア）は一方を変更したらもう一方も確認する。対応ファイル一覧は[docs/development/development.md](docs/development/development.md)の「プラットフォーム対応ファイル」参照
- `agent-basics/*.md`（配布ルール本体）を改訂する際、`docs/guide/claude-code-guide.md`に要約・ステップ数などが再掲されていることが多い。本体変更前に`grep`で参照箇所を確認する
- 依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使う。`UV_FROZEN`はCI/make内で自動適用される

## 関連ドキュメント

- @README.md
- @docs/index.md
- @docs/guide/claude-code.md
- @docs/guide/claude-code-guide.md
- @docs/guide/security.md
- @docs/development/development.md
