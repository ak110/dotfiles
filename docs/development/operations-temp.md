# 運用機能: Linuxの一時領域

## Linuxの一時ディレクトリのディスク上への移動と経過削除

このホストの`/tmp`はメモリー上のtmpfsであり、エージェントの作業とテストの一時ファイルが標準でそこへ溜まるとメモリーとswapの空きが無くなる。
容量不足による検証の失敗、バックグラウンドタスクとユーザーのsystemdマネージャーの強制終了、`/tmp`に置いたhookのセッション状態ファイルの消失が実際に起きた。

`.bashrc`は非対話シェルでも、Linuxに限り`TMPDIR`を`${XDG_CACHE_HOME:-$HOME/.cache}/agent-toolkit/tmp`へ設定する。
作成できない場合は`TMPDIR`を設定しない。
指すディレクトリが無いとPythonの`tempfile`は警告なしに`/tmp`へ戻るためである。
Windowsは一時ディレクトリがディスク上にあるため対象外であり、`.bashrc`はWindowsへ配布しない。

ディスク上の一時領域はtmpfsと違って再起動で空にならないため、`~/.config/user-tmpfiles.d/dotfiles-tmp.conf`が7日より古い項目を削除する。
7日はmanaged-tempの自動削除の期限に合わせた値である。
対象は`agent-toolkit/tmp`配下だけで、兄弟のmanaged-tempは自身の自動削除が扱う。
`setup_tmpfiles_clean.py`は役割によらず全Linuxホストでこのタイマーを有効化する。

HOMEを差し替えて子プロセスを起動する隔離は、2か所に独立して実装している。
テストの環境隔離（`agent-toolkit/agent_toolkit/_testing/isolation.py`）と、旧版更新検証（`scripts/check_update_dotfiles_upgrade.py`の`_isolated_env`）である。
後者はPEP 723スクリプトで前者を読み込めないため、隔離を変える場合は両方を確かめる。
どちらも、一時領域が実ホーム配下にあっても実ホームの設定とツール配置を読み書きしないよう、実ホームを探索起点・参照先とする経路を遮断する。

- 祖先探索: 一時領域が実ホーム配下にあると、HOMEを差し替えた子のmiseが祖先の`~/.config/mise/config.toml`を未信頼のプロジェクト設定として読んで終了コード1になる。
  テストは`MISE_CEILING_PATHS`を各テストの`tmp_path`へ、旧版更新検証は検証rootへ設定して探索を止める。
  この失敗のため、エージェントは一時領域を実ホーム配下へ置いたテストの失敗を避けようとして`/tmp`を選んでいた
- 親から引き継ぐ`PATH`: 旧版更新検証は子の`PATH`から実ホーム配下の要素を除く。
  実ホームの`~/.local/bin`や`~/.nvm/...`が残ると、更新処理のランチャー整理が実ホームのツール配置を走査する
- 配置先を示す環境変数: `NVM_DIR`、`HERDR_INSTALL_DIR`、`NPM_CONFIG_PREFIX`のように実ホームの配置先を指す値は子へ引き継がない
