# 運用機能: 常駐サービス

## 特定ホストでの常駐サービス自動起動

対象は役割`linux_server`のホストであり、`.chezmoi-source/.chezmoidata.toml`が定める（現在は`euryale`）。
このホストでのみ、`chezmoi apply`後処理がsystemd user service`atk-serve.service`を配置して有効化する。

- 待受はローカルのみで、ポート28766を使う
  - 公開はApacheリバースプロキシ（`/etc/apache2/sites-enabled/mysettings-443.conf`、本リポジトリの管理対象外）がBasic Auth付きで行う。
    サブパス公開時は`X-Forwarded-Prefix`ヘッダー付与かつプレフィクスを保持したまま転送する構成を前提とする
  - ホスト固有の待受設定と、計画ファイル・セッションの参照元はunitへ書かず
    `atk config`の`serve_*`キーで与える
    - 同じ後処理が`pytools/_internal/setup_atk_serve.py`の`_SERVE_SETTINGS`（`serve_host`と両画面の`remote_hosts`）を統一設定へ保存し、
      値を変えた場合はサービスを再起動する。
      この3キーは`update-dotfiles`ごとに宣言値へ戻り、他のキーと他用途の設定は保持する
    - 以前chezmoiが配布した`~/.config/agent-toolkit/serve.toml`は配布を止めた後も残り、`atk serve`の起動時の自動移行が
      `serve.toml.migrated`へ改名する。
      移行前の値を失わないよう、`pytools/_internal/removed_paths.py`の`_REMOVED_PATHS`による一括削除の対象にしない
- unitは`Environment=PATH`で`~/.local/bin`を先頭に置き、miseのshimより実体を持つコマンドを優先する。
  `ExecStart`を絶対パスで書いても、起動されたプログラムが実行ファイル名で解決する外部コマンドには及ばないためである
- Web UIはサービス専用ランチャー`~/.local/bin/atk-serve`を経由して起動する
  - agent-toolkitプラグインはバージョン付きディレクトリへ展開されるためunitへ絶対パスを固定で書き込めない。
    ランチャーは`~/dotfiles/agent-toolkit`の`agent_toolkit/atk.py`を絶対パスで直接参照し、プラグインキャッシュの配置を前提としない
  - `uv`はサービス実行環境のPATHに無いため、導入時に解決した絶対パスをランチャーへ埋め込む。
    miseのshimはサービス実行環境でバージョン未解決となり起動しないため優先しない
  - このランチャー生成処理は2026年7月27日（1116f984）まで`~/.local/bin/atk`へランチャーを生成しており、改名時に旧名が残存した。
    `post_apply`の旧配布物削除がこの旧ランチャーと`atk.cmd`を除去し、dotfilesホストの`atk`を作業ツリー版へ解決させる
  - `install-claude.sh`もagent-toolkit単体のユーザー向けに同じ`~/.local/bin/atk`へ別系統のラッパーを生成するが、dotfilesホストでは実行しない前提であり、この生成処理とは無関係である
- lingerが無効な場合はログアウトで停止する。
  常駐させるには`sudo loginctl enable-linger <user>`を手動実行する
- 計画ビューアー統合に伴う`/cpv/`の一度限りの手動移行（Apacheの設定は管理対象外のため`chezmoi apply`では変更されない）は、次のどちらかとする。
  `/cpv/`の`ProxyPass`・`ProxyPassReverse`をポート28765から28766へ向け直し、`RequestHeader set X-Forwarded-Prefix /cpv`を維持して`/cpv/`を残す。
  または`/cpv/`の設定ブロックを削除する。
  変更後は`sudo apachectl configtest`で構文を確認してから`sudo systemctl reload apache2`を実行する。
  既存の`/atk/`を開くエンドユーザーのアクセス先と表示は変わらない

### 公開単位ごとの反映と稼働版の確認

利用環境が古い版のままにならないよう、公開単位ごとに次を行う。

1. CIと必要なリリースの検収結果から、ホストが追跡する公開系統の完全OIDを渡す。
   developを追跡するホストには検収済みのdevelop、masterを追跡するホストには検収済みのmasterのOIDを使う。
   主checkoutの現在のHEADでは代用しない。
   配布版は `git show <公開OID>:agent-toolkit/.claude-plugin/plugin.json` の `version` を使い、OIDとともに記録する。
2. 対象ホストで `update-dotfiles` を実行し、成功を確認する。
   稼働中のunitを使うホストでは `systemctl --user restart atk-serve.service` を実行する。
   `systemctl --user show atk-serve.service --property=LoadState --property=ActiveState --property=SubState` で稼働を確認する。
   unitが未導入なら新設しない。
   異常時は `systemctl --user status atk-serve.service` と `journalctl --user -u atk-serve.service` を確認する。
3. プラグインとMCPを読み込み直し、agents_serverの新しい受付世代を起動する。
   巡回の統括は `process-loop.md` の手順5で同じ会話を再開してから次を実行する。
   `atk serve versions --published <そのホスト向け公開OID>` が、対象サービスの全てで一致を返すことを確かめる。
   サービスを導入していないホストではそのサービスだけを `--skip-serve` または `--skip-agents-server` で外す。
   照会に失敗しただけのサービスは外さない。

`atk serve` は実プロセスが起動時に確定したcheckoutのOIDを返す。
起動対象に未コミットの変更がある時はOIDを確定しない。
`agents_server` は現在の受付世代に認証した接続で問い合わせ、実プロセスが返した世代と登録済みの配布版を、公開OIDの配布版とコードから求めた世代と比べる。
再起動の終了コードやHTTP 200だけでは反映を確認できない。
各段階の結果と未完了の操作を記録し、失敗した時は成功済みのpush・リリースをやり直さず未完了段階から再開する。

### 画面で稼働版を確認する

dotfilesのcheckoutから起動した `atk serve` では、公開版と稼働版が異なるサービスを画面上部に表示する。
公開版はcheckoutが追跡するoriginのブランチから取得する。
未公開のローカルコミットは比較に使わない。
「版を再確認」を押すか画面を開き直すと再照会し、一致すれば表示が消える。
公開先に接続できない、公開コミットのオブジェクトがローカルに無い、サービスに接続できないなどの場合は、取得不能と表示する。
日常の端末確認も `atk serve versions` を使う。
取得不能の場合はネットワーク・`update-dotfiles logs`・サービスの稼働状態を確認する。
サービスの運用担当は公開後の案内で、この表示と再確認の操作を必要なユーザーへ伝える。
