"""このディレクトリ配下のテストが共有するヘルパー。

セッション状態ファイルの読込、transcriptファイルの生成、配送本文の出所標識の検証を提供する。
fixtureは`conftest.py`に置き、テストからimportして使うヘルパーは本ファイルへ置く
（リポジトリ内に`conftest.py`が複数あるとモジュール名が衝突し、
型検査がimport元を一意に解決できないため）。
"""

import json
import pathlib
import re

SESSION_STATE_FILENAME_TEMPLATE = "claude-agent-toolkit-{session_id}.json"

_DELIVERY_TAG_PATTERN = re.compile(
    r'\A<cross-session-message from="(?P<sender>[^"]+)" nonce="(?P<nonce>[0-9a-f]{16})">\n'
    r"(?P<body>.*)\n</cross-session-message>\Z",
    re.DOTALL,
)


def delivery_payload(delivered: str) -> str:
    """agents_serverが配送した本文の出所標識を検証し、囲まれた逐語の本文を返す。"""
    matched = _DELIVERY_TAG_PATTERN.fullmatch(delivered)
    assert matched is not None, delivered
    assert matched["nonce"] not in matched["body"]
    return matched["body"]


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    """テスト用一時ディレクトリからセッション状態を読み込む。"""
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
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
