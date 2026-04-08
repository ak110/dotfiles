# カスタム指示

## コマンド

```bash
make setup    # 初回セットアップ
make format   # 整形 + 軽量lint + 自動修正（開発時の手動実行用）
make test     # 全チェック実行（これが通ればコミット可）
make update   # 依存アップグレード＋全チェック (pinactによるアクション更新含む)
```

## アーキテクチャ

- chezmoi管理のdotfilesリポジトリ
- `.chezmoiroot` でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離
- `.chezmoi-source/` 内が chezmoi のソースディレクトリ (`dot_` prefix → `~/.*` にデプロイ)
- `pytools/` — Pythonコマンドラインツール群 (uv tool installでインストール)
- テンプレートからリポジトリルートのファイルを参照する場合は `{{ .chezmoi.workingTree }}` を使用
  - 例: `{{ include (joinPath .chezmoi.workingTree "pyproject.toml") }}`

### プラットフォーム対応ファイル

以下のファイルはLinux/Windowsで対になっている。一方を変更する場合はもう一方も確認すること。

- `bin/executable_update-dotfiles`
  ↔ `bin/executable_update-dotfiles.cmd`
- `run_after_post-apply.sh.tmpl`
  ↔ `run_after_post-apply-windows.ps1.tmpl`
- `share/claude_settings_json_managed.posix.json`
  ↔ `share/claude_settings_json_managed.win32.json`
- `install.sh`
  ↔ `install.ps1`
- `install-claude.sh`
  ↔ `install-claude.ps1`

新しいOS別 `run_*` スクリプトを追加する場合は `.chezmoiignore` にも除外エントリを追加すること。

### Windows PowerShell スクリプトの注意事項

- `.ps1.tmpl` は `.gitattributes` で `eol=crlf` を指定済み（Windows PowerShell 5.1 は LF 改行だと構文解析に失敗する）
- 全スクリプト冒頭に `Set-StrictMode -Version Latest` と `$ErrorActionPreference = 'Stop'` を記述すること

### ディレクトリ構造の注意

本リポジトリには `.claude` を含むディレクトリが3系統あり、取り違えると影響範囲が全く異なる事故につながる。指示を受けた際はどの階層を指すか必ず確認すること。

- `.chezmoi-source/dot_claude/` — 配布元。chezmoi が `~/.claude/` にデプロイする。ここを書き換えると `chezmoi apply` 後に全環境へ反映される (グローバルユーザー設定の原本)
- `~/.claude/` — デプロイ先 (個人ホーム)。`chezmoi apply` で上書きされるため直接編集してはならない。
  ユーザーが「`~/.claude` の設定を変えて」と言った場合、実際に編集すべきは上記の `.chezmoi-source/dot_claude/` である
- `.claude/` (本リポジトリルート) — dotfiles リポ自身の Claude Code プロジェクト設定 + claudize テンプレート置き場。配布対象外で、このリポジトリで作業する Claude にしか影響しない

chezmoi はドットプレフィックスのディレクトリ (`.claude/` など) を自動無視するため `.chezmoi-source/dot_claude/` と衝突しない。

### ユーザー指示の解釈

- 用語・パスが曖昧な場合は推測で進めず、必ず確認する
- 特に `.claude` 系ディレクトリに関する指示は上記の通り混同しやすいので注意
- 「グローバル」「ユーザースコープ」「本プロジェクト用」のどれかを明示してもらうと取り違えにくい
- 確認して決まった運用ルールは CLAUDE.md や `rules/` 配下に追記し、次セッションへ引き継ぐ

### Claude Code フック実装の配置先 (個人フック vs プラグイン)

本リポジトリには Claude Code の PreToolUse フックを書ける場所が 2 系統あり、
新しいチェックや自動許可ロジックを追加するときはどちらへ入れるか判断する必要がある。
迷ったら推測せず必ずユーザーへ確認する。

- `scripts/claude_hook_pretooluse.py` (個人フック)
  - chezmoi 経由で自分の `~/.claude/settings.json` にのみマージされる
  - 本人の dotfiles 環境でしか動かない。他人には配布されない
  - 向いているチェック: dotfiles 固有の運用前提に依存するもの
    (例: `~/.claude/` が chezmoi 配布先である前提、個人の命名規約・ディレクトリ構成など)
- `plugins/edit-guardrails/` (プラグイン)
  - `.claude-plugin/marketplace.json` 経由で他人にも配布される
  - `claude plugin install edit-guardrails@ak110-dotfiles` でインストールされる
  - 向いているチェック: 他人にも役立つ汎用的な制約・自動化
    (例: 一般的な文字化け検出、一般的な PowerShell 互換性チェック、Claude Code 標準パスに対する操作)

判断基準は以下のとおり。

- 他人にも役立つ汎用的な機能 → プラグイン
- dotfiles 固有の前提に依存する機能 → 個人フック
- 類似のチェックが既に片方に存在する場合はそちらへ統合する (SSOT 原則)

判断後の付随作業として、以下のように配置先ごとに異なる箇所を更新する。

- プラグインに入れた場合: `.claude/rules/plugins.md` のチェックリストに従い
  `plugin.json` の `version` bump と `marketplace.json` との SSOT 同期を行う
- 個人フックに入れた場合: `share/claude_settings_json_managed.posix.json` /
  `share/claude_settings_json_managed.win32.json` の `matcher` に新しい
  ツール名を追加する必要があるか確認する

### GitHub Actionsのピン留め (pinact)

- 全アクションはコミットハッシュでピン留め（pinactで管理）
- ローカル更新: `make update-actions`（mise経由で`pinact run --update --min-age 1`を実行）
- CI検証: `go install pinact@v3.9.0` + `pinact run --check`（バージョン固定）
- pinactのCIバージョンを更新する場合は全プロジェクトのワークフローを一括更新すること

## rules・skills 記述時の注意

rules や skills などのドキュメントは LLM のコンテキストへ直接投入されるため、記述した内容がそのまま生成候補に影響する。以下の点に留意して書く。

- 悪い例をそのまま書かない: 不適切な表現や禁止パターンを本文に載せると、その文字列がコンテキストに取り込まれて逆に生成候補へ出やすくなる。具体例が必要な場合でも抽象化した表現にとどめる
- 禁止事項と推奨事項を明確に分離する: 禁止事項を大量に並べると推奨事項と混同されるおそれがあるため、セクションや箇条書きの階層で区切って書く
  - 実例として agent.md の記述スタイル節では、禁止項目の各行冒頭に `NG: ` を繰り返し付けて、推奨事項と視覚的にも語彙的にも明確に区別している
- 実例として `.chezmoi-source/dot_claude/rules/agent-basics/agent.md` の記述スタイル節では、NG 表現の具体例をあえて列挙せず「不適切な表現の具体例はルールファイルに記載しない」と明記している

## 外部ツール仕様の確認

- 本リポジトリで扱うツールの設定や最新仕様を参照する場合は `context7` MCP を優先する
- 対象例: chezmoi / mise / uv / pnpm / pinact / pre-commit など
- 呼び出し順: `resolve-library-id` → `query-docs`
- 知識のスナップショットではなく最新ドキュメントを確認する

## 関連ドキュメント

- @README.md
- @docs/claude-code.md
- @docs/security.md
- @docs/development.md
