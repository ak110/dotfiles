"""このディレクトリ配下のテストが共有するヘルパー。

セッション状態ファイルの読込、transcriptファイルの生成、配送本文の出所標識の検証、置換で公開した記録の読込を提供する。
fixtureは`conftest.py`に置き、テストからimportして使うヘルパーは本ファイルへ置く
（リポジトリ内に`conftest.py`が複数あるとモジュール名が衝突し、
型チェッカーがimport元を一意に解決できないため）。
"""

import json
import pathlib
import re
import sys

from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common.session_state import state_path

SESSION_STATE_FILENAME_TEMPLATE = "claude-agent-toolkit-{session_id}.json"

_AUTO = _message_format.AUTO_INSERTED_ELEMENT
_DELIVERY_TAG_PATTERN = re.compile(
    rf"\A<{_AUTO}"
    r'(?=[^>]*\ssource="agents-server")'
    r'(?=[^>]*\skind="delivery")[^>]*>\n'
    rf"(?P<body>.*)\n</{_AUTO}>\Z",
    re.DOTALL,
)
_AUTO_OPENING_PATTERN = re.compile(rf'<{_AUTO}(?P<attributes>(?:\s+[a-z][a-z-]*="[^"]*")*)>')
_AUTO_ATTRIBUTE_PATTERN = re.compile(r'\s+([a-z][a-z-]*)="([^"]*)"')
_RESOURCE_PREFIX_PATTERN = re.compile(
    rf'\A<{_AUTO}(?=[^>]*\ssource="agent-toolkit")(?=[^>]*\skind="plugin-resources")[^>]*>\n'
    rf".*?\n</{_AUTO}>\n\n(?P<delivery>.*)\Z",
    re.DOTALL,
)


def read_published(path: pathlib.Path) -> str | None:
    """別プロセスが一時ファイルからの置換で公開した記録の本文を返す。まだ読めなければNoneを返す。

    Windowsでは置換した直後の名前を、置換したプロセスが閉じるまで開けない（PermissionError）。
    存在だけを待って読むと、この共有違反で失敗する。
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError, PermissionError:
        return None


def link_directory(link: pathlib.Path, target: pathlib.Path) -> None:
    """`link`を`target`へ解決するディレクトリのリンクとして作る。

    Windowsのシンボリックリンク作成は特権か開発者モードを要するため、通常ユーザーでも作れるジャンクションを使う。
    どちらも`Path.resolve`で`target`へ解決する。
    """
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel,import-error
        import _winapi  # noqa: PLC0415 -- Windows専用で、他のOSではimportできない。

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def auto_message_opening_attributes(text: str) -> dict[str, str]:
    """自動挿入本文の開始タグから属性を名前で取得する。"""
    opening = _AUTO_OPENING_PATTERN.search(text)
    assert opening is not None, text
    pairs = _AUTO_ATTRIBUTE_PATTERN.findall(opening["attributes"])
    attributes = dict(pairs)
    assert len(attributes) == len(pairs), text
    return attributes


def delivery_payload(delivered: str) -> str:
    """agents_serverが配送した本文の出所標識を検証し、囲まれた逐語の本文を返す。"""
    if resources := _RESOURCE_PREFIX_PATTERN.fullmatch(delivered):
        delivered = resources["delivery"]
    matched = _DELIVERY_TAG_PATTERN.fullmatch(delivered)
    assert matched is not None, delivered
    return matched["body"]


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    """テスト用一時ディレクトリからセッション状態を読み込む。"""
    del state_dir
    path = state_path(session_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_transcript(directory: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    """エントリ列をJSONL形式のtranscriptへ書き込む。"""
    transcript = directory / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )
    return transcript
