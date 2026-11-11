# アーキテクチャ: 対象環境と補完

## 対象環境とスクリプトの配置

本dotfilesは以下の二者を想定している。
配布対象と開発対象でサポート範囲が異なるため、ファイル追加時にどちら用かを確認。

- エンドユーザー: Linux+Windows（配布対象。`install.sh`/`install.ps1`/`install-claude.sh`/`install-claude.ps1`/
  chezmoi管理ファイルはすべて両OS対応とする）
- 開発者: Linuxのみ（`make test`/prek/CIの開発系ジョブはLinux前提。macOS/Windowsでのローカル開発は非対応で構わない）

この区別に基づき、スクリプトの配置先を以下のように分ける。

- `scripts/`: prek・Makefile・pyfltr・CIなど開発とCIの工程から呼ばれるスクリプトの置き場
  - chezmoiで配布せず、エンドユーザー環境では実行しない。
    Linux前提で書いてよいが、CIのWindows jobで動くもの（`scripts/check_update_dotfiles_upgrade.py`）はWindowsでも動く書き方とする
  - 例: `scripts/check-templates.sh`・`scripts/check-cmd-encoding.sh`・
    `scripts/check-ps1-bom.sh`・`scripts/run-psscriptanalyzer.sh`
- `libexec/`: `bin/`のランチャー、chezmoiの後処理、systemd unit、Claude Codeのhook定義など他のプログラムから起動され、
  `pytools`パッケージの外でエンドユーザー環境（LinuxとWindows）で動く実行ファイルの置き場
  - chezmoiで配布せず、`~/dotfiles`の作業ツリーから直接実行する。
    両OSで動く書き方とする
  - 例: `libexec/update_dotfiles.py`・`libexec/claude-hook-pretooluse.ps1`・`libexec/keep-awake.ps1`
- `bin/`: ユーザーのPATHに追加して使うコマンド。
  リポジトリ直下でgit管理し、
  `~/dotfiles/bin`（Linux）/`%USERPROFILE%\dotfiles\bin`（Windows）にPATHを通す
  - 両OS対応のコマンドはLinux版とWindows版（`.cmd`／`.ps1`）を併置する
  - 例: `bin/update-dotfiles`↔`bin/update-dotfiles.cmd`

判断に迷ったら「エンドユーザー環境で実行されるか」を基準に決める。
開発とCIの工程からしか動かないなら`scripts/`、
エンドユーザー環境で他のプログラムから起動されるなら`libexec/`、PATHから直接起動するなら`bin/`が適切。

### `bin/`を変えるときの確認

単純なコマンドラッパーのペアは`scripts/new_bin_cmd.py <name> <command...>`で生成できる。
`bin/<name>`と`bin/<name>.cmd`を生成する。

`bin/`直下のスクリプトを追加・移設・削除する際は、以下を同時に見直す。

- Linuxの配布設定: `.bashrc`のPATH追加行
- Windowsの配布設定: `pytools/_internal/setup_bin_path.py`によるユーザーPATH追記
- `.github/workflows/ci.yaml`から呼ぶ`scripts/ci_verify_linux.sh`・`scripts/ci_verify_windows.ps1`

新しいOS別`run_*`スクリプトを追加する場合は`.chezmoiignore`にも除外エントリを追加。

## bash補完（`completions/`）

対象はLinux/bashのみ。
Windowsではbash補完を提供しない。

補完スクリプトはログイン時の`register-python-argcomplete`実行コストを避けるため事前生成してリポジトリにチェックインする。
`completions/*.bash`を`.bashrc`がすべて`source`する。
コマンド追加時に`.bashrc`を編集する必要はない。
`scripts/gen_completions.py`は生成先を2箇所へ分岐して書き込む。
通常はpyfltrのcustom formatterから統合生成ランナー経由で実行する。
`pyproject.toml`の`[project.scripts]`由来のコマンドは`completions/_pytools.bash`へ書き込む。
`agent-toolkit/scripts/*.py`のうちargcompleteマーカーを持つスクリプトが対象で、
対応するbashラッパーが`agent-toolkit/scripts/`配下に存在するコマンド（`atk`等）に限る。
これらの補完は`scripts/gen_completions.py`が`agent-toolkit/completions/atk.bash`へ書き込む。

新しいCLIに補完を追加する場合は、CLIモジュールへのマーカー配置と`enable_completion()`呼び出しをコード側コメントに従い追加し、
補完スクリプトを再生成する。

### 補完スクリプトの再生成・検証

```bash
uv run --frozen python scripts/sync_generated_files.py  # 全生成物を冪等同期
uv run --frozen pyfltr fast                             # 高速ツールと生成物を同期
```

手書き補完が必要な場合（`bin/`配下コマンドのうち`gen_completions.py`の収集対象外のものなど）は
`completions/<name>.bash`を新規追加する。
`_`プレフィックスのファイルは自動生成物の慣習として予約する。
`agent-toolkit/completions/atk.bash`は`gen_completions.py`の自動生成対象のため、この手順は当てはまらない。

## Windows PowerShellスクリプトの注意事項

- `.ps1.tmpl`は`.gitattributes`で`eol=crlf`を指定している（Windows PowerShell 5.1はLF改行だと構文解析に失敗する）
- 全スクリプト冒頭に`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を記述
