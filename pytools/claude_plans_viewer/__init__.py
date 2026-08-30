# PYTHON_ARGCOMPLETE_OK
r"""Claude Codeの計画Markdownをブラウザで一覧・閲覧するローカルHTTPビューア。

既定ではローカルと各リモートホストについて、`$(atk config get private_notes)/plans`と
`~/.claude/plans`の二rootを統合表示する。`--root`、環境変数または設定ファイルでrootを
明示すると、ローカル側の既定二rootだけを指定した単一rootへ置き換える。

SSHポートフォワード越しにWindows側のブラウザから参照することを想定し、
外部CDNに依存せずサーバー側でMarkdownを安全なHTMLへ変換し、
同梱したMermaidと画像コンテキストのSVGをブラウザー側で図として表示する。
`--remote-host`を複数指定すると、SSH経由で各ホストの既定二rootもwatchdogで監視して
左ペインへ統合表示する。

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
from pytools.claude_plans_viewer._cli import main  # noqa: F401  (entry-point再export)
