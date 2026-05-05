# アーキテクチャ

本リポジトリはchezmoi管理のdotfilesリポジトリ。
主要なコンポーネントとその役割を以下に示す。

- `.chezmoiroot`でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離する
- `.chezmoi-source/`内がchezmoiのソースディレクトリ（`dot_`プレフィックス→`~/.*`にデプロイ）
- `pytools/` — Pythonコマンドラインツール群（`uv tool install`でインストール）
- `scripts/` — リポジトリ開発専用のスクリプト置き場（pre-commit/Makefileから呼ばれる。配布対象外）
- テンプレートからリポジトリルートのファイルを参照する場合は`{{ .chezmoi.workingTree }}`を使用
  - 例: `{{ include (joinPath .chezmoi.workingTree "pyproject.toml") }}`

## 開発者と利用者の対象環境

本dotfilesは以下の二者を想定している。配布対象と開発対象でサポート範囲が異なるため、ファイル追加時にどちら用かを確認。

- 利用者: Linux+Windows（配布対象。`install.sh`/`install.ps1`/`install-claude.sh`/`install-claude.ps1`/
  chezmoi管理ファイルはすべて両OS対応とする）
- 開発者: Linuxのみ（`make test`/pre-commit/CIの開発系ジョブはLinux前提。macOS/Windowsでのローカル開発は非対応で構わない）

この区別に基づき、スクリプトの配置先を以下のように分ける。

- `scripts/` — pre-commitやMakefileからしか呼ばれない開発者向けツール。chezmoiで配布しない。Linux前提で書いてよい
  - 例: `scripts/check-templates.sh`・`scripts/check-cmd-encoding.sh`・
    `scripts/check-ps1-bom.sh`・`scripts/run-psscriptanalyzer.sh`
- `bin/` — ユーザーのPATHに追加して使うコマンド。リポジトリ直下でgit管理し、
  `~/dotfiles/bin`（Linux）/`%USERPROFILE%\dotfiles\bin`（Windows）にPATHを通す。
  Linux/Windows両対応に注意し、Windows向けには`.cmd`版を併置する
  - 例: `bin/update-dotfiles`↔`bin/update-dotfiles.cmd`

判断に迷ったら「他者の環境で直接実行されるか」で切り分ける。pre-commit経由でしか動かないなら`scripts/`が適切。

## プラットフォーム対応ファイル

以下のファイルはLinux/Windowsで対になっている。一方を変更する場合はもう一方を確認。

| Linux | Windows |
| --- | --- |
| `bin/c` | `bin/c.cmd` |
| `bin/ccusage` | `bin/ccusage.cmd` |
| `bin/claude-code-viewer` | `bin/claude-code-viewer.cmd` |
| `bin/sonnet` | `bin/sonnet.cmd` |
| `bin/update-dotfiles` | `bin/update-dotfiles.cmd` |
| `.chezmoi-source/run_after_post-apply.sh.tmpl` | `.chezmoi-source/run_after_post-apply-windows.ps1.tmpl` |
| `share/claude_settings_json_managed.posix.json` | `share/claude_settings_json_managed.win32.json` |
| `install.sh` | `install.ps1` |
| `install-claude.sh` | `install-claude.ps1` |

単純なコマンドラッパーのペアは`scripts/new-bin-cmd.py <name> <command...>`で生成できる。
`bin/<name>`と`bin/<name>.cmd`を生成し、本ファイルのペア一覧を自動更新する。

`bin/`直下のスクリプトを追加・移設・削除する際は、以下を同時に見直す。

- Linuxの配布経路: `.bashrc`のPATH追加行
- Windowsの配布経路: `pytools/_internal/setup_bin_path.py`によるユーザーPATH追記
- `.github/workflows/ci.yaml`の「主要ファイルの存在確認」ステップ

新しいOS別`run_*`スクリプトを追加する場合は`.chezmoiignore`にも除外エントリを追加。

## bash補完（`completions/`）

対象はLinux/bashのみ。Windowsではbash補完を提供しない。

補完スクリプトはログイン時の`register-python-argcomplete`実行コストを避けるため事前生成してリポジトリにチェックインする。
`completions/*.bash`を`.bashrc`がすべて`source`する。コマンド追加時に`.bashrc`を編集する必要はない。

補完スクリプトの再生成・検証:

```bash
uv run --script scripts/gen-completions.py          # 再生成
uv run --script scripts/gen-completions.py --check  # 検証（pre-commitフックで自動実行）
```

新しいCLIに補完を追加する場合:

1. CLIモジュール先頭に`# PYTHON_ARGCOMPLETE_OK`マーカーを配置
2. `parser.parse_args()`の直前で`pytools._internal.cli.enable_completion(parser)`を呼び出す処理を追加
3. 補完スクリプトを再生成（上記コマンドを参照）

手書き補完が必要な場合（`bin/`配下コマンドなど）は`completions/<name>.bash`を新規追加。
`_`プレフィックスのファイルは自動生成物の慣習として予約する。
Windows専用CLI（`runAsAdmin`・`regExport`）はLinux環境では`ArgumentParser`生成前に終了するため、
マーカーを付けず補完対象外としている。

## Windows PowerShellスクリプトの注意事項

- `.ps1.tmpl`は`.gitattributes`で`eol=crlf`を指定している（Windows PowerShell 5.1はLF改行だと構文解析に失敗する）
- 全スクリプト冒頭に`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を記述

## ホーム配下のファイルを編集する前の確認

`~/.config/`・`~/.claude/`などホーム直下のファイルを編集する場合、
まず`chezmoi managed | grep <相対パス>`で配布対象かを確認。
配布対象であれば`.chezmoi-source/`側を編集（直接編集は次回`chezmoi apply`で上書きされる）。
設定の出所調査には`git config --show-origin --get <key>`も有効。
