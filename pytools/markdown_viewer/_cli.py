"""コマンドライン引数解析とエントリーポイント。"""

import argparse
import hashlib
import logging
import pathlib
import sys
import tempfile
import webbrowser

from pytools._internal.cli import enable_completion, setup_logging
from pytools.markdown_viewer import _render

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(
        description="Markdownファイルを既定ブラウザで表示する。",
    )
    parser.add_argument(
        "file",
        type=pathlib.Path,
        help="表示するMarkdownファイルのパス",
    )
    enable_completion(parser)
    return parser.parse_args(argv)


def _output_path(source: pathlib.Path) -> pathlib.Path:
    """入力Markdownの絶対パスから一時HTMLの出力先を返す。

    同じ入力ファイルは同じ出力先へ上書きし、一時ディレクトリの肥大を抑える。
    """
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    return pathlib.Path(tempfile.gettempdir()) / "markdown-viewer" / f"{digest}.html"


def _main(argv: list[str] | None = None) -> int:
    """エントリポイント。

    `pyproject.toml`の`[project.scripts]`から
    `markdown-viewer = "pytools.markdown_viewer:_main"`の形で参照されるため、
    関数名はアンダースコア付きのまま維持する（変更すると配布物との互換が破綻する）。
    """
    setup_logging()
    args = parse_args(argv)
    source: pathlib.Path = args.file
    if not source.is_file():
        print(f"ファイルが見つかりません: {source}", file=sys.stderr)
        return 1

    source_abs = source.resolve()
    text = source_abs.read_text(encoding="utf-8")
    body_html = _render.render_body(text)
    css_path = _render.resolve_css_path()
    css_text = css_path.read_text(encoding="utf-8") if css_path is not None else ""
    # `<base href>`にディレクトリのfile URIを渡し、画像など相対パス参照を入力ファイルの親基準で解決させる。
    # `Path.as_uri()`はディレクトリでも末尾スラッシュを付けないため、ここで明示的に追加する。
    base_uri = source_abs.parent.as_uri() + "/"
    document = _render.build_html_document(
        body_html=body_html,
        title=source_abs.name,
        base_uri=base_uri,
        css_text=css_text,
    )

    output = _output_path(source_abs)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")

    try:
        opened = webbrowser.open(output.as_uri())
    except webbrowser.Error as e:
        print(f"ブラウザ起動エラー: {e}", file=sys.stderr)
        print(f"HTMLパス: {output}", file=sys.stderr)
        return 1
    if not opened:
        print("ブラウザを起動できませんでした。", file=sys.stderr)
        print(f"HTMLパス: {output}", file=sys.stderr)
        return 1

    logger.info("Opened %s -> %s", source_abs, output)
    return 0
