# 運用機能: Linuxの一時領域

## Linuxの一時ディレクトリのディスク上への移動と経過削除

このホストの`/tmp`はメモリー上のtmpfsであり、エージェントの作業とテストの一時ファイルが標準でそこへ溜まるとメモリーとswapの空きが無くなる。
容量不足による検証の失敗、バックグラウンドタスクとユーザーのsystemdマネージャーの強制終了、`/tmp`に置いたhookのセッション状態ファイルの消失が実際に起きた。

`.bashrc`は非対話シェルでも、Linuxに限り`TMPDIR`を`${XDG_CACHE_HOME:-$HOME/.cache}/agent-toolkit/tmp`へ設定する。
XDGのキャッシュディレクトリはアプリケーションごとに分けるため、managed-temp（`agent-toolkit/managed-temp`）と同じ`agent-toolkit`配下に置く。
Pythonの`tempfile`、pytest、Node、Rust、Claude Code本体とhookは`TMPDIR`を一時ディレクトリの置き場所として使うため、プログラムごとの設定なしにディスク上へ移る。
ディレクトリは権限0700で作成する。
作成できない場合は`TMPDIR`を設定しない。
指すディレクトリが無いとPythonの`tempfile`は警告なしに`/tmp`へ戻るためである。
Windowsは一時ディレクトリがディスク上にあるため対象外であり、`.bashrc`はWindowsへ配布しない。

ディスク上の一時領域はtmpfsと違って再起動で空にならないため、`~/.config/user-tmpfiles.d/dotfiles-tmp.conf`の`d %C/agent-toolkit/tmp 0700 - - 7d`が古い項目を削除する。
削除するのは、最終更新・最終アクセス・状態変更（ディレクトリは最終更新と最終アクセス）の全てが7日より古い項目である（`man 5 tmpfiles.d`）。
読み取るだけの項目が最終アクセスによって残るのは、最終アクセスの時刻を更新するマウントの場合に限る（`noatime`のマウントでは最終更新と状態変更だけで判定される）。
7日はmanaged-tempの自動削除の期限に合わせた値である。
対象は`agent-toolkit/tmp`配下だけで、兄弟のmanaged-tempは自身の自動削除が扱う。
削除はユーザーの`systemd-tmpfiles-clean.timer`が実行する。
`setup_tmpfiles_clean.py`は役割によらず全Linuxホストでこのタイマーを有効化する。
`systemctl`が無い環境、またはユーザーのsystemdマネージャーへ接続できない環境では理由と次の操作を示して見送り、systemdのユーザーセッションがある環境で`update-dotfiles`を再実行する。

HOMEを差し替えて子プロセスを起動する隔離は、2か所に独立して実装している。
テストの環境隔離（`agent-toolkit/agent_toolkit/_testing/isolation.py`）と、旧版更新検証（`scripts/check_update_dotfiles_upgrade.py`の`_isolated_env`）である。
後者はPEP 723スクリプトで前者を読み込めないため、隔離を変える場合は両方を確かめる。
どちらも、一時領域が実ホーム配下にあっても実ホームの設定とツール配置を読み書きしないよう、実ホームを探索起点・参照先とする経路を遮断する。

- 祖先探索: miseは作業ディレクトリの祖先から設定を探す。
  一時領域が実ホーム配下にあると、HOMEを差し替えた子のmiseが祖先の`~/.config/mise/config.toml`を未信頼のプロジェクト設定として読んで終了コード1になる。
  テストは`MISE_CEILING_PATHS`を各テストの`tmp_path`へ、旧版更新検証は検証rootへ設定して探索を止める。
  この失敗のため、エージェントは一時領域を実ホーム配下へ置いたテストの失敗を避けようとして`/tmp`を選んでいた
- 親から引き継ぐ`PATH`: 旧版更新検証は子の`PATH`から実ホーム配下の要素を除き、検証と更新処理が名前で起動する`uv`、`uvx`、`chezmoi`を検証homeの`~/.local/bin`へ複製する。
  実ホームの`~/.local/bin`や`~/.nvm/...`が残ると、更新処理のランチャー整理が実ホームのツール配置を走査する。
  Windowsの`%LOCALAPPDATA%\Microsoft\WindowsApps`は残す。
  更新処理がmise本体の導入に使うwingetのapp execution aliasがあり、OSが管理する起動口でユーザーのツール配置ではないためである
- 配置先を示す環境変数: `NVM_DIR`、`HERDR_INSTALL_DIR`、npmの全体導入先（`NPM_CONFIG_PREFIX`）のように実ホームの配置先を指す値は子へ引き継がない。
  旧版更新検証はnpmの全体導入先を、Linuxでは検証homeの`~/.local`、Windowsでは検証homeの`%APPDATA%\npm`へ向ける。
  Linuxでは実ホームのNodeを外すとシステムのNodeの書き込めない導入先が選ばれるためである。
  Windowsのnpmは導入先の直下へランチャーを置くため、導入先を`PATH`へ加えて通常のユーザー環境を再現する

ホストの設定を使うテストは`host_environ`と`restore_host_environment`で、`MISE_CEILING_PATHS`もホストの値へ戻す。
