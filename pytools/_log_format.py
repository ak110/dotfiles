"""post-apply 系モジュールで共有するログフォーマットヘルパー。

`pytools.post_apply` は logging.basicConfig の format に `"  %(message)s"` を
指定して全ログ行に列 2 のインデントを付与する。各ステップが詳細行を出力する際は
`format_status()` を使ってメッセージ側に追加で 4 スペースを持たせ、最終的に列 6
の `<対象>: <状態>` 形式に揃える。

ホームディレクトリ配下のファイルを表示する場合は `home_short()` で `~/...` に
短縮する (絶対パスはノイズになりやすいため)。
"""

from pathlib import Path


def format_status(target: str, state: str) -> str:
    """`    <target>: <state>` 形式の詳細行を返す。

    post-apply の logging.basicConfig が行頭に "  " を付けるため、
    本関数は追加で 4 スペースを持たせ、最終的に列 6 の出力にする。
    """
    return f"    {target}: {state}"


def home_short(path: Path) -> str:
    """`Path.home()` 配下なら `~/...` に短縮する。配下外はそのまま `str(path)`。

    Path.home() 自身は `~` を返す。シンボリックリンク解決は行わない
    (見た目を整えるだけの用途のため、resolve でユーザーの意図しないパスへ
    展開されないよう素のパスで判定する)。
    """
    home = Path.home()
    if path == home:
        return "~"
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)
