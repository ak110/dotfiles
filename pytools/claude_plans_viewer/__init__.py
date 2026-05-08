# PYTHON_ARGCOMPLETE_OK
r"""Claude Codeの`~/.claude/plans/*.md`をブラウザで一覧・閲覧するローカルHTTPビューア。

SSHポートフォワード越しにWindows側のブラウザから参照することを想定し、
外部CDNに依存せずサーバー側でMarkdownをHTMLへ変換する。
`--remote-host`を複数指定すると、SSH経由で各ホストの`~/.claude/plans/`も
watchdogで監視して左ペインへ統合表示する。

設定ファイル:
    既定パスは`platformdirs.user_config_dir("pytools", appauthor=False)`配下の
    `claude-plans-viewer.toml`（Linuxでは`~/.config/pytools/claude-plans-viewer.toml`、
    Windowsでは`%LOCALAPPDATA%\\pytools\\claude-plans-viewer.toml`）。
    環境変数`CLAUDE_PLANS_VIEWER_CONFIG`で上書きできる。
    キーはトップレベル直書きで、`root`・`host`・`port`・`remote-hosts`を受け付ける
    （未知キーは警告ログを記録して無視する）。
    各オプションの解決優先順位は「CLI引数 > 環境変数 > 設定ファイル > 組み込み既定値」。
"""

from pytools.claude_plans_viewer._app import create_app  # noqa: F401  (再export)
from pytools.claude_plans_viewer._cli import _main  # noqa: F401  (entry-point再export)
