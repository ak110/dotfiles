# アーキテクチャ

本リポジトリはchezmoi管理のdotfilesリポジトリ。
主要なコンポーネントとその役割を以下に示す。

- `.chezmoiroot`でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離する
- `.chezmoi-source/`内がchezmoiのソースディレクトリ（`dot_`プレフィックス→`~/.*`にデプロイ）
- `.chezmoi-source/dot_claude/`: Claude Code用のユーザー設定。`~/.claude/`へデプロイする
- `.chezmoi-source/dot_codex/`: Codex用のユーザー設定。`~/.codex/`へデプロイする
- `pytools/`: Pythonコマンドラインツール群（`uv tool install`でインストール）
- `rust/`: Rust製コマンドラインツール群（CIでビルドしGitHub Releaseへ配布）
- `scripts/`: リポジトリ内部から呼ばれるスクリプト置き場（pre-commit・Makefile・Claude Codeフック等。配布対象外）
- テンプレートからリポジトリルートのファイルを参照する場合は`{{ .chezmoi.workingTree }}`を使用
  - 例: `{{ include (joinPath .chezmoi.workingTree "pyproject.toml") }}`

## 開発者と利用者の対象環境

本dotfilesは以下の二者を想定している。配布対象と開発対象でサポート範囲が異なるため、ファイル追加時にどちら用かを確認。

- 利用者: Linux+Windows（配布対象。`install.sh`/`install.ps1`/`install-claude.sh`/`install-claude.ps1`/
  chezmoi管理ファイルはすべて両OS対応とする）
- 開発者: Linuxのみ（`make test`/pre-commit/CIの開発系ジョブはLinux前提。macOS/Windowsでのローカル開発は非対応で構わない）

この区別に基づき、スクリプトの配置先を以下のように分ける。

- `scripts/`: pre-commit・Makefile・Claude Codeフックなどリポジトリ内部から呼ばれるスクリプト置き場
  - chezmoiで配布しない。Linux前提で書いてよい
  - 例: `scripts/check-templates.sh`・`scripts/check-cmd-encoding.sh`・
    `scripts/check-ps1-bom.sh`・`scripts/run-psscriptanalyzer.sh`・`scripts/claude_hook_pretooluse.py`
- `bin/`: ユーザーのPATHに追加して使うコマンド。リポジトリ直下でgit管理し、
  `~/dotfiles/bin`（Linux）/`%USERPROFILE%\dotfiles\bin`（Windows）にPATHを通す
  - 両OS対応のコマンドはLinux版とWindows版（`.cmd`／`.ps1`）を併置する
  - 例: `bin/update-dotfiles`↔`bin/update-dotfiles.cmd`

判断に迷ったら「他者の環境で直接実行されるか」で切り分ける。pre-commit経由でしか動かないなら`scripts/`が適切。

単純なコマンドラッパーのペアは`scripts/new-bin-cmd.py <name> <command...>`で生成できる。
`bin/<name>`と`bin/<name>.cmd`を生成する。

`bin/`直下のスクリプトを追加・移設・削除する際は、以下を同時に見直す。

- Linuxの配布経路: `.bashrc`のPATH追加行
- Windowsの配布経路: `pytools/_internal/setup_bin_path.py`によるユーザーPATH追記
- `.github/workflows/ci.yaml`の「主要ファイルの存在確認」ステップ

新しいOS別`run_*`スクリプトを追加する場合は`.chezmoiignore`にも除外エントリを追加。

## bash補完（`completions/`）

対象はLinux/bashのみ。Windowsではbash補完を提供しない。

補完スクリプトはログイン時の`register-python-argcomplete`実行コストを避けるため事前生成してリポジトリにチェックインする。
`completions/*.bash`を`.bashrc`がすべて`source`する。コマンド追加時に`.bashrc`を編集する必要はない。
`scripts/gen-completions.py`は生成先を2箇所へ分岐して書き込む。
`pyproject.toml`の`[project.scripts]`由来のコマンドは`completions/_pytools.bash`へ書き込む。
`agent-toolkit/scripts/*.py`のうちargcompleteマーカーを持つスクリプトが対象で、
対応するbashラッパーが`agent-toolkit/bin/`配下に存在するコマンド（`atk`等）に限る。
これらは`agent-toolkit/completions/atk.bash`へ書き込む。

新しいCLIに補完を追加する場合は、CLIモジュールへのマーカー配置と`enable_completion()`呼び出しをコード側コメントに従い追加し、
補完スクリプトを再生成する。

### 補完スクリプトの再生成・検証

```bash
uv run --script scripts/gen-completions.py          # 再生成
uv run --script scripts/gen-completions.py --check  # 検証（pre-commitフックで自動実行）
```

手書き補完が必要な場合（`bin/`配下コマンドのうち`gen-completions.py`の収集対象外のものなど）は
`completions/<name>.bash`を新規追加する。`_`プレフィックスのファイルは自動生成物の慣習として予約する。
`agent-toolkit/completions/atk.bash`は`gen-completions.py`の自動生成対象のため、この手順は当てはまらない。

## Windows PowerShellスクリプトの注意事項

- `.ps1.tmpl`は`.gitattributes`で`eol=crlf`を指定している（Windows PowerShell 5.1はLF改行だと構文解析に失敗する）
- 全スクリプト冒頭に`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を記述

## ホーム配下のファイルを編集する前の確認

`~/.config/`・`~/.claude/`などホーム直下のファイルを編集する場合、
まず`chezmoi managed | grep <相対パス>`で配布対象かを確認。
配布対象であれば`.chezmoi-source/`側を編集（直接編集は次回`chezmoi apply`で上書きされる）。
設定の出所調査には`git config --show-origin --get <key>`も有効。
