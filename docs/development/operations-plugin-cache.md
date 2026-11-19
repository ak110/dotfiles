# 運用機能: Claude Code plugin cache

## Claude Code plugin cacheの旧版削除

UACの管理者承認モードのWindowsで昇格したプロセスから実行した回は、管理サービスの起動確認と公開を省いて成功記録も残さず、次の非昇格の実行で行う（[design-windows-permissions.md](design-windows-permissions.md)）。

Claude Codeは`plugin install`と`plugin update`で`~/.claude/plugins/cache/<marketplace>/<name>/<version>/`を新設するだけで、旧版を削除しない。
`claude plugin`にもキャッシュ整理のサブコマンドは無く、2026年9月26日のeuryaleではagent-toolkitだけで75版（約3.9GB）が残っていた。

- 対象は`~/.claude/plugins/installed_plugins.json`に載っているmarketplaceとpluginの組だけとし、`installPath`が指す現行版は削除しない
- 現行でない版は、導入時刻の順で次に新しい版（後継）の導入から7日が経過し、かつ稼働中の参照が無い場合に削除する。
  後継の無い版は残す
  - 起動中のClaude Codeセッションは起動時に解決した版ディレクトリを`hooks.json`と`.mcp.json`から参照し続けるため、参照中に削除するとそのセッションのhookとMCPが失敗する。
  - 稼働中の参照は自ユーザーのプロセスから判定する。
    次のいずれかに当たる版は保持し、参照が終わった後の`update-dotfiles`で削除する。
    保持した版と理由は`update-dotfiles logs`に残る
    - プロセスの作業ディレクトリ、実行ファイル、command line、環境変数`CLAUDE_PLUGIN_ROOT`がその版の配下を指す（その版から起動したMCPとhookを含む）
    - その版が現行だった期間（その版の導入から後継版の導入まで）に起動したClaude Codeセッションが稼働中である
    - Claude Codeのプロセスの参照先を取得できない
- 現行版の必須ファイルの欠落を修復する「Claude Code pluginのインストール」工程も、導入先を削除して再導入するため同じ判定を使う。
  導入先を参照するプロセスか稼働中のClaude Codeセッション（起動時刻を問わない）があれば修復を延期し、欠落ファイル、保持した導入先、再開の操作を警告する
- 導入時刻には各版の`.claude-plugin/plugin.json`のmtimeを用いる。
  版ディレクトリ自体のmtimeは`uv run`が`.venv`などを作成するたびに更新され、版の順序と一致しない（74版中74件が不一致）。
  `claude-plugins-official`配下はcommit hashなど自然順に並ばない版名を持つため、版名の順序ではなく導入時刻で後継を決める
- 本工程は`installPath`の更新後に判定するため、「Claude Code pluginのインストール」工程の直後に置く
- `~/.codex/plugins/cache`は対象外とする。
  Codexの旧版はCodex pluginの導入処理が同じ猶予期間と導入時刻の選び方で削除する（[設計記録](design-hosts-plugin-lifecycle.md#codexのlocal-plugin導入)）
- シンボリックリンクとディレクトリジャンクションは辿らず削除しない
