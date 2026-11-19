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
- `libexec/`: `bin/`のランチャー、chezmoiの後処理、systemd unit、Claude Codeのhook定義など他のプログラムから起動され、
  `pytools`パッケージの外でエンドユーザー環境（LinuxとWindows）で動く実行ファイルの置き場
  - chezmoiで配布せず、`~/dotfiles`の作業ツリーから直接実行する。
    両OSで動く書き方とする
- `bin/`: ユーザーのPATHに追加して使うコマンド。
  リポジトリ直下でgit管理し、
  `~/dotfiles/bin`（Linux）/`%USERPROFILE%\dotfiles\bin`（Windows）にPATHを通す
  - 両OS対応のコマンドはLinux版とWindows版（`.cmd`／`.ps1`）を併置する

判断に迷ったら「エンドユーザー環境で実行されるか」を基準に決める。
開発とCIの工程からしか動かないなら`scripts/`、
エンドユーザー環境で他のプログラムから起動されるなら`libexec/`、PATHから直接起動するなら`bin/`が適切。

### `bin/`を変えるときの確認

`bin/`直下のスクリプトを追加・移設・削除する際は、以下を同時に見直す。

- Linuxの配布設定: `.bashrc`のPATH追加行
- Windowsの配布設定: `pytools/_internal/setup_bin_path.py`によるユーザーPATH追記
- `.github/workflows/ci.yaml`から呼ぶ`scripts/ci_verify_linux.sh`・`scripts/ci_verify_windows.ps1`

新しいOS別`run_*`スクリプトを追加する場合は`.chezmoiignore`にも除外エントリを追加。

## bash補完（`completions/`）

対象はLinux/bashのみ。
Windowsではbash補完を提供しない。

補完スクリプトはログイン時の`register-python-argcomplete`実行コストを避けるため事前生成してリポジトリにチェックインする。

手書き補完が必要な場合（`bin/`配下コマンドのうち`gen_completions.py`の収集対象外のものなど）は
`completions/<name>.bash`を新規追加する。
`_`プレフィックスのファイルは自動生成物の慣習として予約する。

## Windows PowerShellスクリプトの注意事項

- `.ps1.tmpl`は`.gitattributes`で`eol=crlf`を指定している（Windows PowerShell 5.1はLF改行だと構文解析に失敗する）
- 全スクリプト冒頭に`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を記述
