# 運用機能: Claude Code plugin cache

## Claude Code plugin cacheの旧版削除

directory型marketplaceでは、原本と導入先の配布内容が一致していれば同版の`plugin install`を省く。
開発用の仮想環境や`__pycache__`などを比較から除き、原本の編集や導入先の欠落は次回の再同期対象にする。
agents_serverのウォームアップでは依存検査を毎回行い、コードと起動条件が前回成功時と同じ場合だけMCPの起動確認を省く。
成功記録はOSのユーザーキャッシュ領域の`dotfiles/post-apply/`に処理ごとの固定ファイルとして保存する。
読めない記録は再処理で回復し、古い記録は次の成功時に置き換える。
各工程の所要時間は通常のpost-apply出力と`update-dotfiles logs`で確認できる。

Claude Codeは`plugin install`と`plugin update`で`~/.claude/plugins/cache/<marketplace>/<name>/<version>/`を新設するだけで、旧版を削除しない。
`claude plugin`にもキャッシュ整理のサブコマンドは無く、2026年9月26日のeuryaleではagent-toolkitだけで75版（約3.9GB）が残っていた。
`post_apply`の「Claude Code plugin cacheの旧版削除」工程（`pytools/_internal/prune_claude_plugin_cache.py`）がこれを整理する。

- 対象は`~/.claude/plugins/installed_plugins.json`に載っているmarketplaceとpluginの組だけとし、`installPath`が指す現行版は削除しない
- 現行でない版は、導入時刻の順で次に新しい版（後継）の導入から7日が経過し、かつ稼働中の参照が無い場合に削除する。
  後継の無い版は残す
  - 起動中のClaude Codeセッションは起動時に解決した版ディレクトリを`hooks.json`と`.mcp.json`から参照し続けるため、参照中に削除するとそのセッションのhookとMCPが失敗する。
    経過日数は参照の終了を表さないため、削除の候補を選ぶ条件として使う
  - 稼働中の参照は`pytools/_internal/claude_plugin_references.py`が自ユーザーのプロセスから判定する。
    次のいずれかに当たる版は保持し、参照が終わった後の`update-dotfiles`で削除する。
    保持した版と理由は`update-dotfiles logs`に残る
    - プロセスの作業ディレクトリ、実行ファイル、command line、環境変数`CLAUDE_PLUGIN_ROOT`がその版の配下を指す（その版から起動したMCPとhookを含む）
    - その版が現行だった期間（その版の導入から後継版の導入まで）に起動したClaude Codeセッションが稼働中である
    - Claude Codeのプロセスの参照先を取得できない
- 現行版の必須ファイルの欠落を修復する「Claude Code pluginのインストール」工程（`install_claude_plugins.py`の`_ensure_plugin_cache_complete`）も、導入先を削除して再導入するため同じ判定を使う。
  導入先を参照するプロセスか稼働中のClaude Codeセッション（起動時刻を問わない）があれば修復を延期し、欠落ファイル、保持した導入先、再開の操作を警告する。
  Claude Codeのセッションと導入先を参照するプロセスを全て終了してから`update-dotfiles`を再実行して修復する
- 導入時刻には各版の`.claude-plugin/plugin.json`のmtimeを用いる
  - 版ディレクトリ自体のmtimeは`uv run`が`.venv`などを作成するたびに更新され、版の順序と一致しない（74版中74件が不一致）。
    `plugin.json`のmtimeは版の順序と一致する
  - `claude-plugins-official`配下はcommit hashなど自然順に並ばない版名を持つため、導入時刻で後継を決める
  - `plugin.json`を欠く不完全な版は版ディレクトリのmtimeで代替する
- `installPath`の更新後に判定するため、工程は「Claude Code pluginのインストール」の直後に置く
- `~/.codex/plugins/cache`は導入処理が1版だけを保持するため対象外とする。
  シンボリックリンクとディレクトリジャンクションは辿らず削除しない
