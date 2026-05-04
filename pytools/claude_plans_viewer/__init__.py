# PYTHON_ARGCOMPLETE_OK
"""Claude Codeの`~/.claude/plans/*.md`をブラウザで一覧・閲覧するローカルHTTPビューア。

SSHポートフォワード経由でWindows側のブラウザから参照することを想定し、
外部CDNに依存せずサーバー側でMarkdownをHTMLへ変換する。
Markdown→HTML変換はraw HTMLをエスケープする設定とし、
`~/.claude/plans/`配下の内容がスクリプトとして実行されないようにする。

`--remote-host`を複数指定すると、SSH経由で各ホストの`~/.claude/plans/`を
watchdog経由で監視し、ローカル分と同じ左ペインへ統合表示する。
リモート側は`uv run --no-project --script -`でヘルパーを実行し、
`python`／`python3`のPATH差を吸収する。

設定値の優先順位は「CLI引数 > 環境変数 > 組み込み既定値」とし、
環境ごとの差分は環境変数で吸収できるようにしている。

- `CLAUDE_PLANS_VIEWER_ROOT`: Markdownのルートディレクトリ
- `CLAUDE_PLANS_VIEWER_HOST`: bindアドレス
- `CLAUDE_PLANS_VIEWER_PORT`: 待受ポート
- `CLAUDE_PLANS_VIEWER_REMOTE_HOSTS`: コロン区切りのSSH接続先一覧

リモート監視はwatchdogによるpush方式を採用する。
ポーリング方式は対象ファイル数が増えた場合や低リソースホストでのCPU/SSH接続コストが
懸念されるため、SSH越しに長時間watchプロセスを常駐させて差分イベントだけを配信する設計としている。

モジュール構成は責務単位で以下に分割している。

- `_assets`: SPA・PWA・リモートヘルパーの埋め込みアセット
- `_state`: SSE購読者・debounce状態・リモートホストキャッシュなど共有状態
- `_local`: ローカルファイル探索・watchdog連携・Markdown変換・CSS解決
- `_remote`: SSH経由のリモートホスト統合（リモートwatch・リモートファイル取得）
- `_app`: Quartアプリ生成とAPIハンドラ
- `_cli`: コマンドライン引数解析とエントリーポイント

公開APIは`create_app`と`_main`（`pyproject.toml`の`[project.scripts]`参照）のみ。
内部識別子はサブモジュール経由（例: `from pytools.claude_plans_viewer import _state`）で参照する。
"""

from pytools.claude_plans_viewer._app import create_app  # noqa: F401  (再export)
from pytools.claude_plans_viewer._cli import _main  # noqa: F401  (entry-point再export)
