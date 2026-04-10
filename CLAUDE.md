# カスタム指示

## コマンド

```bash
make setup    # 初回セットアップ
make format   # 整形 + 軽量lint + 自動修正（開発時の手動実行用）
make test     # 全チェック実行（これが通ればコミット可）
make update   # 依存アップグレード＋全チェック (pinactによるアクション更新含む)
```

## 依存関係の方針

- サプライチェーン攻撃対策として、`UV_FROZEN=1`を`Makefile`とCIワークフロー（該当ジョブ・ステップ）で有効化し、`uv sync`/`uv run`が`uv.lock`を再resolveせずそのまま使うようにしている
  - 開発者のシェルでは`UV_FROZEN`を設定しない前提のため、依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使えばよい
  - `make update`も内部で自動的にUV_FROZENを外すため、そのまま実行してよい
  - 詳細な運用方針は`docs/development.md`の「UV_FROZENによるlockfile尊重」セクションを参照

## アーキテクチャ

- chezmoi管理のdotfilesリポジトリ
- `.chezmoiroot` でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離
- `.chezmoi-source/` 内がchezmoiのソースディレクトリ（`dot_` prefix → `~/.*` にデプロイ）
- `pytools/` — Pythonコマンドラインツール群（uv tool installでインストール）
- `scripts/` — リポジトリ開発専用のスクリプト置き場（pre-commit/Makefileから呼ばれる。配布対象外）
- テンプレートからリポジトリルートのファイルを参照する場合は `{{ .chezmoi.workingTree }}` を使用
  - 例: `{{ include (joinPath .chezmoi.workingTree "pyproject.toml") }}`

### 開発者と利用者の対象環境

本dotfilesは以下の二者を想定している。配布対象と開発対象でサポート範囲が異なるため、ファイル追加時にどちら用か意識すること。

- 利用者: Linux + Windows（配布対象。`install.sh`/`install.ps1`/`install-claude.sh`/`install-claude.ps1`/chezmoi管理ファイルは全て両OS対応とする）
- 開発者: Linuxのみ（`make test`/pre-commit/CIの開発系ジョブはLinux前提。macOS/Windowsでのローカル開発は非対応で構わない）

この区別に基づき、スクリプトの配置先を以下のように決める。

- `scripts/` — pre-commitやMakefileからしか呼ばれない開発者向けツール。chezmoiで配布しない。Linux前提で書いてよい
  - 例: `scripts/check-templates.sh`, `scripts/check-cmd-encoding.sh`,
    `scripts/check-ps1-bom.sh`, `scripts/run-psscriptanalyzer.sh`
- `.chezmoi-source/bin/executable_*` — 利用者のホームに配布するコマンド。Linux/Windows両対応に注意し、Windows向けには `.cmd` 版を併置する
  - 例: `executable_update-dotfiles` ↔ `executable_update-dotfiles.cmd`

判断に迷ったら「他人の環境で直接実行されるか」で切り分ける。pre-commit経由でしか動かないなら `scripts/` が適切。

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

- `.ps1.tmpl` は `.gitattributes` で `eol=crlf` を指定済み（Windows PowerShell 5.1はLF改行だと構文解析に失敗する）
- 全スクリプト冒頭に `Set-StrictMode -Version Latest` と `$ErrorActionPreference = 'Stop'` を記述すること

### ディレクトリ構造の注意

本リポジトリには `.claude` を含むディレクトリが3系統あり、取り違えると影響範囲が全く異なる事故につながる。指示を受けた際はどの階層を指すか必ず確認すること。

- `.chezmoi-source/dot_claude/` — 配布元。chezmoiが `~/.claude/` にデプロイする。ここを書き換えると `chezmoi apply` 後に全環境へ反映される（グローバルユーザー設定の原本）
- `~/.claude/` — デプロイ先（個人ホーム）。`chezmoi apply` で上書きされるため直接編集してはならない。
  ユーザーが「`~/.claude` の設定を変えて」と言った場合、実際に編集すべきは上記の `.chezmoi-source/dot_claude/` である
- `.claude/`（本リポジトリルート）— dotfilesリポ自身のClaude Codeプロジェクト設定 + claudizeテンプレート置き場。配布対象外で、このリポジトリで作業するClaudeにしか影響しない

chezmoiはドットプレフィックスのディレクトリ (`.claude/` など) を自動無視するため `.chezmoi-source/dot_claude/` と衝突しない。

### ユーザー指示の解釈

- 用語・パスが曖昧な場合は推測で進めず、必ず確認する
- 特に `.claude` 系ディレクトリに関する指示は上記の通り混同しやすいので注意
- 「グローバル」「ユーザースコープ」「本プロジェクト用」のどれかを明示してもらうと取り違えにくい
- 確認して決まった運用ルールはCLAUDE.mdや `rules/` 配下に追記し、次セッションへ引き継ぐ

### Claude Code フック実装の配置先 (個人フック vs プラグイン)

本リポジトリにはClaude CodeのPreToolUseフックを書ける場所が2系統あり、
新しいチェックや自動許可ロジックを追加するときはどちらへ入れるか判断する必要がある。
迷ったら推測せず必ずユーザーへ確認する。

- `scripts/claude_hook_pretooluse.py`（個人フック）
  - chezmoi経由で自分の `~/.claude/settings.json` にのみマージされる
  - 本人のdotfiles環境でしか動かない。他人には配布されない
  - 向いているチェック: dotfiles固有の運用前提に依存するもの
   （例: `~/.claude/` がchezmoi配布先である前提、個人の命名規約・ディレクトリ構成など）
- `plugins/agent-toolkit/`（プラグイン）
  - `.claude-plugin/marketplace.json` 経由で他人にも配布される
  - `claude plugin install agent-toolkit@ak110-dotfiles` でインストールされる
  - 向いているチェック: 他人にも役立つ汎用的な制約・自動化
   （例: 一般的な文字化け検出、一般的なPowerShell互換性チェック、Claude Code標準パスに対する操作）

判断基準は以下のとおり。

- 他人にも役立つ汎用的な機能 → プラグイン
- dotfiles固有の前提に依存する機能 → 個人フック
- 類似のチェックが既に片方に存在する場合はそちらへ統合する（SSOT原則）

判断後の付随作業として、以下のように配置先ごとに異なる箇所を更新する。

- プラグインに入れた場合: `.claude/rules/plugins.md` のチェックリストに従い
  `plugin.json` の `version` bumpと `marketplace.json` とのSSOT同期を行う
- 個人フックに入れた場合: `share/claude_settings_json_managed.posix.json` /
  `share/claude_settings_json_managed.win32.json` の `matcher` に新しい
  ツール名を追加する必要があるか確認する

### GitHub Actionsのピン留め (pinact)

- 全アクションはコミットハッシュでピン留め（pinactで管理）
- ローカル更新: `make update-actions`（mise経由で`pinact run --update --min-age 1`を実行）
- CI検証: `go install pinact@v3.9.0` + `pinact run --check`（バージョン固定）
- pinactのCIバージョンを更新する場合は全プロジェクトのワークフローを一括更新すること

## rules・skills 記述時の注意

rulesやskillsなどのドキュメントはLLMのコンテキストへ直接投入されるため、記述した内容がそのまま生成候補に影響する。以下の点に留意して書く。

- 悪い例をそのまま書かない: 不適切な表現や禁止パターンを本文に載せると、その文字列がコンテキストに取り込まれて逆に生成候補へ出やすくなる。具体例が必要な場合でも抽象化した表現にとどめる
- 禁止事項と推奨事項を明確に分離する: 禁止事項を大量に並べると推奨事項と混同されるおそれがあるため、セクションや箇条書きの階層で区切って書く
  - 実例としてagent.mdの記述スタイル節では、禁止項目の各行冒頭に接頭辞 `NG:` を繰り返し付けて推奨事項と区別している
- 実例として `.chezmoi-source/dot_claude/rules/agent-basics/agent.md` の記述スタイル節では、NG表現の具体例を列挙していない
  - 「不適切な表現の具体例はルールファイルに記載しない」と明記して、コンテキスト汚染を回避している

## 関連ドキュメント

- @README.md
- @docs/claude-code.md
- @docs/security.md
- @docs/development.md
