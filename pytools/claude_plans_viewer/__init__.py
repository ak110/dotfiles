# PYTHON_ARGCOMPLETE_OK
"""Claude Codeの`~/.claude/plans/*.md`をブラウザで一覧・閲覧するローカルHTTPビューア。

SSHポートフォワード越しにWindows側のブラウザから参照することを想定し、
外部CDNに依存せずサーバー側でMarkdownをHTMLへ変換する。
`--remote-host`を複数指定すると、SSH経由で各ホストの`~/.claude/plans/`も
watchdogで監視して左ペインへ統合表示する。
"""

from pytools.claude_plans_viewer._app import create_app  # noqa: F401  (再export)
from pytools.claude_plans_viewer._cli import _main  # noqa: F401  (entry-point再export)
