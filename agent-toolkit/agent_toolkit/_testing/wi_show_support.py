"""WIのshow試験で共有する準備処理。"""

import datetime
import pathlib

from agent_toolkit._atk.wi.layout import entry_path

_SUMMARY_TITLE = "要約照会でも省略せずに表示する長いAWIの表題" * 4


_SUMMARY_AWI = ["### fb-001.md [inbox]", _SUMMARY_TITLE]


_SUMMARY_UWI = ["### uwi-001.md [inbox/answered]", "表示する質問の先頭行"]


def _write_awi_state_file(
    notes: pathlib.Path,
    state: str,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "state本文",
) -> pathlib.Path:
    """年月配置へ指定stateのAWIを書き込み、絶対パスを返す。"""
    path = entry_path(notes, filename, fallback_date=datetime.date(2026, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: awi\nstate: {state}\ntarget_repo: {target_repo}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _write_awi_processing_file(
    notes: pathlib.Path,
    filename: str,
    target_repo: str = "github.com/example/foo",
    body: str = "processing本文",
) -> pathlib.Path:
    """processing配下に1ファイルを書き込み、絶対パスを返す（`_write_awi_state_file`の薄いラッパー）。"""
    return _write_awi_state_file(notes, "processing", filename, target_repo=target_repo, body=body)
