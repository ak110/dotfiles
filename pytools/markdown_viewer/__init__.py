# PYTHON_ARGCOMPLETE_OK
"""単一Markdownファイルを既定ブラウザでスタイル付きHTML表示するCLIツール。

`share/vscode/markdown.css`を取り込んでスタイリングしたHTMLを一時ディレクトリへ生成し、
`webbrowser.open()`で既定ブラウザに開く。サーバーは常駐させず、起動のたびに静的変換のみを行う。
"""

from pytools.markdown_viewer._cli import main  # noqa: F401  (entry-point再export)
