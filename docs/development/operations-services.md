# 運用機能: 常駐サービス

## 特定ホストでの常駐サービス自動起動

対象は役割`linux_server`のホストであり、`.chezmoi-source/.chezmoidata.toml`が定める（現在は`euryale`）。
このホストでのみ、`chezmoi apply`後処理がsystemd user service`atk-serve.service`を配置して有効化する。
`atk serve`は「AWI」「計画ファイル」「セッション」の3画面を同じナビゲーションから提供する。

- 待受はローカルのみで、ポート28766を使う
  - ホスト上のブラウザーかSSHポート転送経由に加え、Apacheリバースプロキシ
    （`/etc/apache2/sites-enabled/mysettings-443.conf`、本リポジトリの管理対象外）を使用する。
    Basic Auth付きで`https://tqzh.tk/atk/`へ公開する
  - サブパス公開時は`X-Forwarded-Prefix`ヘッダー付与かつプレフィクスを保持したまま
    転送する構成を前提とする。
    Web UI側は`pytilpack.quart.ProxyFix`でこれを解釈する
  - ホスト固有の待受設定と、計画ファイル・セッションの参照元はunitへ書かず
    `atk config`の`serve_*`キーで与える
    - 同じ後処理が`pytools/_internal/setup_atk_serve.py`の`_SERVE_SETTINGS`（`serve_host`と両画面の`remote_hosts`）を統一設定へ保存し、
      値を変えた場合はサービスを再起動する。
      この3キーは`update-dotfiles`ごとに宣言値へ戻り、他のキーと他用途の設定は保持する
    - 以前chezmoiが配布した`~/.config/agent-toolkit/serve.toml`は配布を止めた後も残り、`atk serve`の起動時の自動移行が
      `serve.toml.migrated`へ改名する。
      移行前の値を失わないよう、`post_apply.py`の`_REMOVED_PATHS`による一括削除の対象にしない
- unitは`Environment=PATH`で`%h/.local/bin`、miseのshims、systemdが設定するPATHをこの順に指定する
  - `ExecStart`を絶対パスで書いても、起動されたプログラムが実行ファイル名で解決する外部コマンドには及ばない
  - `~/.local/bin`を先頭へ置き、実体を持つコマンドをmiseのshimより優先する
- Web UIはサービス専用ランチャー`~/.local/bin/atk-serve`を経由して起動する
  - agent-toolkitプラグインはバージョン付きディレクトリへ展開されるためunitへ絶対パスを固定で書き込めない
  - ランチャーは`~/dotfiles/agent-toolkit`の`agent_toolkit/atk.py`を絶対パスで直接参照し、プラグインキャッシュの配置を前提としない
  - `uv`はサービス実行環境のPATHに無いため、導入時に解決した絶対パスをランチャーへ埋め込む
    - 解決順序は`~/.local/bin/uv`（公式インストーラーの導入先）、次にPATH探索とし、
      いずれも得られない場合は設定を見送る
    - miseのshimはサービス実行環境でバージョン未解決となり起動しないため優先しない
  - このランチャー生成処理は2026年7月27日（1116f984）まで`~/.local/bin/atk`へランチャーを生成しており、改名時に旧名が残存した。
    `post_apply`の旧配布物削除がこの旧ランチャーと`atk.cmd`を除去し、dotfilesホストの`atk`を作業ツリー版へ解決させる
  - `install-claude.sh`もagent-toolkit単体のユーザー向けに同じ`~/.local/bin/atk`へ別系統のラッパーを生成するが、dotfilesホストでは実行しない前提であり、この生成処理とは無関係
- 導入処理はrestart後に常駐を確認し、起動しない場合は失敗として`update-dotfiles`の出力へ表示する
- 旧計画ビューアーの`claude-plans-viewer.service`は導入処理が停止と無効化に成功した場合だけunitファイルを削除する
  - 停止できない場合はunitファイルを残して警告を記録し、後続の配置は続行する
- lingerが無効な場合はログアウトで停止する
  - 常駐させるには`sudo loginctl enable-linger <user>`を手動実行する

計画ビューアーの統合に伴い、リバースプロキシーの`/cpv/`は一度限りの手動移行を要する
（Apacheの設定は本リポジトリの管理対象外のため、`chezmoi apply`では変更されない）。

1. `/cpv/`の`ProxyPass`・`ProxyPassReverse`をポート28765からポート28766へ向け直し、
   `RequestHeader set X-Forwarded-Prefix /cpv`を維持して`/cpv/`を残すか、`/cpv/`の設定ブロックを削除する
2. `/cpv/`を残す場合、`https://tqzh.tk/cpv/`は`atk serve`のWI画面を表示する。
   計画ファイル画面は同じベースパス配下の`/cpv/plans`となる
3. `/cpv/`を削除する場合、エンドユーザーは`https://tqzh.tk/atk/plans`へ移動する。
   既存の`/atk/`を開くエンドユーザーのアクセス先と表示は変わらない
4. `sudo apachectl configtest`で構文を確認してから`sudo systemctl reload apache2`を実行する

### fast-forward後のatk serveへの反映

主checkoutのfast-forward後は、そのcheckoutを参照する稼働中の`atk-serve.service`を再起動する。
対象は前節の`linux_server`ホストであり、unitが未導入の環境では実施しない。
`systemctl --user show atk-serve.service --property=LoadState --property=ActiveState --property=SubState`で導入と稼働状態を確認する。
停止中のunitを新たに起動する必要がある場合は、そのホストの運用判断を確認する。

1. `git -C /home/aki/dotfiles rev-parse --short HEAD`で反映対象を記録する。
2. `systemctl --user restart atk-serve.service`を実行し、終了コードを確認する。
3. `systemctl --user show atk-serve.service --property=ActiveState --property=SubState`で`active`と`running`を確認する。
4. `curl --fail --silent --show-error --max-time 10 --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:28766/`でHTTP 200を確認する。

ホストや設定でcheckoutと待受が異なる場合は、unitの`ExecStart`と`atk config`の設定に合わせる。
再起動や稼働確認に失敗した場合は、`systemctl --user status atk-serve.service --no-pager`と`journalctl --user -u atk-serve.service -n 50 --no-pager`で診断を取得する。
反映HEAD、各終了コード、状態とHTTP応答、失敗時の診断を記録して公開担当へ返す。
