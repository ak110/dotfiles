# 開発

## 初回セットアップ

```bash
make setup
```

### 任意: PowerShell スクリプトの検証環境

pre-commitフックの `powershell-analyzer` および `chezmoi template check` の `.ps1.tmpl` 検証は
`pwsh`（PowerShell 7）に依存する。
`pwsh` 上では `PSScriptAnalyzer` モジュールも併せて必要になる。

未導入でもフックは警告を出してスキップするため `make test` は通過する。
ローカルで完全検証したい場合は下記の手順で導入する。検証漏れはCIのtest-linuxジョブで担保する。

Ubuntu/Debianの場合は以下のコマンドで一括導入できる。

```bash
make setup-pwsh
```

macOS:

```bash
brew install --cask powershell
pwsh -NoProfile -Command "Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -SkipPublisherCheck"
```

## チェックの実行

```bash
make test
```

## その他のコマンド

```bash
make format   # 整形 + 軽量lint + 自動修正（開発時の手動実行用）
make update   # 依存アップグレード＋全チェック（pinactによるアクション更新含む）
```

## READMEとdocsの役割分担

本プロジェクトのドキュメントは以下の構成で配置している。

- README.md: 概要・特徴・ドキュメントへのリンク（該当する場合はインストール手順も）を網羅する「玄関」。
  README.mdだけを読めばプロジェクトの目的と使い始めるための入口が把握できる状態を保つ
- docs/guide/: 利用者向けの詳細情報（chezmoiの使い方・Claude Code設定・pytools・SSH・セキュリティなど）
- docs/development/: 開発者向けの情報（セットアップ・チェック実行・アーキテクチャ・リリース手順など）

README.mdとdocs側で概要・特徴・インストール手順が部分的に重複する場合があるが、
README.mdはGitHubトップとして、docs側は公開ドキュメントの入口としてそれぞれ自己完結する必要があるため、この重複は許容する。
本プロジェクトの`docs/index.md`はREADMEへの参照のみに留めており、インストール手順の重複は発生させていない。

変更頻度が低いため二重管理のコストより一貫性・可読性のメリットが上回ると判断した。
変更時は、docs側で同じ情報を再掲している箇所があれば同じコミット内で合わせて更新する。

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

- 利用者: Linux + Windows（配布対象。`install.sh`/`install.ps1`/`install-claude.sh`/`install-claude.ps1`/
  chezmoi管理ファイルは全て両OS対応とする）
- 開発者: Linuxのみ（`make test`/pre-commit/CIの開発系ジョブはLinux前提。macOS/Windowsでのローカル開発は非対応で構わない）

この区別に基づき、スクリプトの配置先を以下のように決める。

- `scripts/` — pre-commitやMakefileからしか呼ばれない開発者向けツール。chezmoiで配布しない。Linux前提で書いてよい
  - 例: `scripts/check-templates.sh`, `scripts/check-cmd-encoding.sh`,
    `scripts/check-ps1-bom.sh`, `scripts/run-psscriptanalyzer.sh`
- `.chezmoi-source/bin/executable_*` — 利用者のホームに配布するコマンド。
  Linux/Windows両対応に注意し、Windows向けには `.cmd` 版を併置する
  - 例: `executable_update-dotfiles` ↔ `executable_update-dotfiles.cmd`

判断に迷ったら「他人の環境で直接実行されるか」で切り分ける。pre-commit経由でしか動かないなら `scripts/` が適切。

### プラットフォーム対応ファイル

以下のファイルはLinux/Windowsで対になっている。一方を変更する場合はもう一方も確認すること。

- `.chezmoi-source/bin/executable_c` ↔ `.chezmoi-source/bin/executable_c.cmd`
- `.chezmoi-source/bin/executable_ccusage` ↔ `.chezmoi-source/bin/executable_ccusage.cmd`
- `.chezmoi-source/bin/executable_claude-code-viewer` ↔ `.chezmoi-source/bin/executable_claude-code-viewer.cmd`
- `.chezmoi-source/bin/executable_sonnet` ↔ `.chezmoi-source/bin/executable_sonnet.cmd`
- `.chezmoi-source/bin/executable_update-dotfiles` ↔ `.chezmoi-source/bin/executable_update-dotfiles.cmd`
- `.chezmoi-source/run_after_post-apply.sh.tmpl` ↔ `.chezmoi-source/run_after_post-apply-windows.ps1.tmpl`
- `share/claude_settings_json_managed.posix.json` ↔ `share/claude_settings_json_managed.win32.json`
- `install.sh` ↔ `install.ps1`
- `install-claude.sh` ↔ `install-claude.ps1`

シンプルなコマンドラッパーのペアは `scripts/new-bin-cmd <name> <command...>` で生成できる。
bashスクリプトと `.cmd` ファイルを生成し、`.chezmoiignore` と本ファイルのペア一覧も自動更新する。

新しいOS別 `run_*` スクリプトを追加する場合は `.chezmoiignore` にも除外エントリを追加すること。

### Windows PowerShell スクリプトの注意事項

- `.ps1.tmpl` は `.gitattributes` で `eol=crlf` を指定済み（Windows PowerShell 5.1はLF改行だと構文解析に失敗する）
- 全スクリプト冒頭に `Set-StrictMode -Version Latest` と `$ErrorActionPreference = 'Stop'` を記述すること

### ディレクトリ構造の注意

本リポジトリには `.claude` を含むディレクトリが3系統あり、取り違えると影響範囲が全く異なる事故につながる。
指示を受けた際はどの階層を指すか必ず確認すること。

- `.chezmoi-source/dot_claude/` — 配布元。chezmoiが `~/.claude/` にデプロイする。
  ここを書き換えると `chezmoi apply` 後に全環境へ反映される（グローバルユーザー設定の原本）
- `~/.claude/` — デプロイ先（個人ホーム）。`chezmoi apply` で上書きされるため直接編集してはならない。
  ユーザーが「`~/.claude` の設定を変えて」と言った場合、実際に編集すべきは上記の `.chezmoi-source/dot_claude/` である
- `.claude/`（本リポジトリルート）— dotfilesリポ自身のClaude Codeプロジェクト設定 + claudizeテンプレート置き場。
  配布対象外で、このリポジトリで作業するClaudeにしか影響しない

chezmoiはドットプレフィックスのディレクトリ（`.claude/` など）を自動無視するため `.chezmoi-source/dot_claude/` と衝突しない。

### ホーム配下のファイルを編集する前の確認

`~/.config/`・`~/.claude/` などホーム直下のファイルを編集する場合、まず `chezmoi managed | grep <相対パス>` で
配布対象か確認する。配布対象なら `.chezmoi-source/` 側を編集すること（直接編集は次回 `chezmoi apply` で上書きされる）。
設定の出所調査には `git config --show-origin --get <key>` も有効。

## Claude Codeフック実装の配置先（個人フック vs プラグイン）

本リポジトリにはClaude CodeのPreToolUseフックを書ける場所が2系統あり、
新しいチェックや自動許可ロジックを追加するときはどちらへ入れるか判断する必要がある。迷ったら推測せず必ずユーザーへ確認する。

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

- プラグインに入れた場合: `.claude/rules/plugins.md` のチェックリストに従い `plugin.json` の `version` bumpと
  `marketplace.json` とのSSOT同期を行う
- 個人フックに入れた場合: `share/claude_settings_json_managed.posix.json`および同`win32.json`の`matcher`に
  新しいツール名を追加する必要があるか確認する

## Claude Code プラグインの自動インストールとmarketplace修復

`update-dotfiles`（`chezmoi apply`後処理）は`pytools/_internal/install_claude_plugins.py`経由で
agent-toolkitプラグインを自動インストール・更新する。
marketplace登録は`ak110/dotfiles`のGitHubショートハンドに統一している。
Claude Codeは慣例ディレクトリ`~/.claude/plugins/marketplaces/ak110-dotfiles/`へ自動cloneする。
同モジュールは`~/.claude/plugins/known_marketplaces.json`と`~/.claude/settings.json`の
`extraKnownMarketplaces`の両方を点検する。
directory型・別repoなどの破損エントリを検出した場合はGitHub型へマイグレーションする。
CLI経由の再登録で解消しない場合はJSON直接書き換えで修復し、
`installLocation`の実体欠落を防ぐため`marketplace update`でgit cloneを誘発する。
marketplace登録の修復責務は本モジュールに一元化している。

プラグインのversion bumpをpushする前に`update-dotfiles`を実行すると、
ローカル`marketplace.json`とGitHubキャッシュの乖離で「更新を検出」ログが出るが実反映はされない。
GitHubキャッシュ側がpush前の旧バージョンのためで、push後に再度`update-dotfiles`を実行すれば解消する。

## GitHub Actionsのピン留め（pinact）

- 全アクションはコミットハッシュでピン留め（pinactで管理）
- ローカル更新: `make update-actions`（mise経由で`pinact run --update --min-age 1`を実行）
- CI検証: `go install pinact@v3.9.0` + `pinact run --check`（バージョン固定）
- pinactのCIバージョンを更新する場合は全プロジェクトのワークフローを一括更新すること

## サプライチェーン攻撃対策

CI/`make`などの自動実行環境で`uv sync`/`uv run`が依存解決を再実行せず`uv.lock`をそのまま使うよう、
環境変数`UV_FROZEN=1`を有効化している。
意図しない再resolveでロックファイルが書き換わるリスクを抑え、
グローバル設定の`exclude-newer`（[docs/guide/security.md](../guide/security.md)参照）と組み合わせて二重防御として機能する。

- `make format`/`make test`/`make setup`は`Makefile`の`export UV_FROZEN := 1`で自動適用される
- CIは`.github/workflows/*.yaml`の`env.UV_FROZEN`で自動適用される
- `git commit`経由のpre-commitフックは`.pre-commit-config.yaml`のlocal hookのentryに`--frozen`を明示している

開発者のシェルでは`UV_FROZEN`を設定しない前提なので、
依存の追加・更新は通常どおり`uv add`/`uv remove`/`uv lock --upgrade-package`を使えばよい。
`make update`も内部で自動的にUV_FROZENを外すため、そのまま実行してよい。

## コミットメッセージ（Conventional Commits）

Conventional Commits形式に従う。ただし記述の方向性があまり変わらないような軽微な修正は`chore`などにしてよい。

特に本プロジェクトはdotfilesをホームディレクトリに反映することが主な機能である。
例えば`.chezmoi-source`配下の文言修正は厳密にはfixになるが、上記の例外として`chore`などを選んでよい。

## rules・skills 記述時の注意

rulesやskillsなどのドキュメントはLLMのコンテキストへ直接投入されるため、記述した内容がそのまま生成候補に影響する。
以下の点に留意して書く。

- 悪い例をそのまま書かない: 不適切な表現や禁止パターンを本文に載せると、
  その文字列がコンテキストに取り込まれて逆に生成候補へ出やすくなる。
  具体例が必要な場合でも抽象化した表現にとどめる
- 禁止事項と推奨事項を明確に分離する: 禁止事項を大量に並べると推奨事項と混同されるおそれがあるため、
  セクションや箇条書きの階層で区切って書く
  - 実例として`styles.md`の「言語と文体」節では、禁止項目の各行冒頭に接頭辞 `NG:` を繰り返し付けて推奨事項と区別している
- 実例として `.chezmoi-source/dot_claude/rules/agent-toolkit/styles.md` では、NG表現の具体例を列挙していない
  - 「不適切な表現の具体例はルールファイルに記載しない」と明記して、コンテキスト汚染を回避している

## その他

### VSCode（`~/.vscode-server/data/Machine/settings.json`）

```json
{
    "python.linting.pylintArgs": [
        "--rcfile=~/dotfiles/share/vscode/pylintrc"
    ]
}
```

### ipython

```bash
uv pip install -r ~/dotfiles/requirements.txt
ipython --profile=ipy
```
