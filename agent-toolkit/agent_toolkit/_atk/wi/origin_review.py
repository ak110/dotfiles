from __future__ import annotations

import hashlib
import json
import pathlib
import re
from collections.abc import Sequence

from agent_toolkit._atk.wi import frontmatter, headings
from agent_toolkit._atk.wi.constants import WI_TYPE_AWI
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._plan.structure import REQUEST_H2_QUOTATION

_NEXT = "独立した検収者が原文とこの版の原稿を照合し、合格した検収結果の絶対パスを--origin-reviewへ指定する"
_SCOPE_WORD = re.compile(
    r"その他もろもろ|その他諸々|などを含む|(?<!何)(?<!なん)とか|全体的に|すべて|全て|あらゆる|全部|一切"
    r"|全レビュー|全画面|全件|全工程|全項目"
)
_REVIEW_HEADINGS = (REQUEST_H2_QUOTATION, "ユーザーコメント", "反映内容と反映先", "完成条件")
_TARGET_FIELDS = ("target_repo", "target_branch", "target_commit")


def _digest(text: str) -> str:
    return hashlib.sha256(frontmatter.normalize_newlines(text).encode("utf-8")).hexdigest()


def _origin(body: str) -> bool:
    return any(_section(body, name) for name in _REVIEW_HEADINGS[:2])


def _section(body: str, name: str) -> str:
    return "\n".join(headings.last_h2_section_lines(body, name) or []).strip("\n")


def _reviewed_parts(body: str) -> tuple[object, ...]:
    tokens = headings.parse_h2_headings(body)
    end = next((token.map[0] for token, _ in tokens if token.level == 0 and token.map), None)
    opening = "\n".join(body.split("\n")[:end]).strip()
    return opening, *(_section(body, name) for name in _REVIEW_HEADINGS)


def _absolute(value: object) -> pathlib.Path:
    if not isinstance(value, str) or not pathlib.Path(value).is_absolute():
        raise ValueError("絶対パスが必要")
    path = pathlib.Path(value)
    if not path.is_file():
        raise ValueError(f"参照ファイルが存在しない: {path}")
    return path


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _verify_record(record: dict[str, object], draft: str, body: str) -> None:
    if record.get("判定") != "合格":
        raise ValueError("原要求の判定が合格ではない")
    if not _nonblank(record.get("根拠")):
        raise ValueError("原要求の判定根拠が無い")
    original = _absolute(record.get("原本")).read_text(encoding="utf-8")
    if not original.strip() or record.get("原本SHA256") != _digest(original):
        raise ValueError("原本が空か、検収時の版と一致しない")
    if "分割原稿" in record:
        group = record["分割原稿"]
        if not isinstance(group, dict) or not group:
            raise ValueError("分割原稿は別の原稿の絶対パスとSHA256を記した辞書で指定する")
        for name, digest in group.items():
            if _digest(_absolute(name).read_text(encoding="utf-8")) != digest:
                raise ValueError(f"分割原稿が検収時の版と一致しない: {name}")
    coverage = record.get("範囲対応")
    if not isinstance(coverage, dict):
        raise ValueError("範囲対応は辞書で指定する（該当なしは空辞書）")
    quoted_origin = "\n".join(
        line for heading in _REVIEW_HEADINGS[:2] for line in headings.last_h2_section_lines(body, heading) or []
    )
    markers = set(_SCOPE_WORD.findall(original)) | set(_SCOPE_WORD.findall(quoted_origin))
    if missing := markers - coverage.keys():
        raise ValueError("原文の範囲語に対応が無い: " + "、".join(sorted(missing)))
    lines = draft.split("\n")
    for marker, detail in coverage.items():
        if not isinstance(detail, dict) or not _nonblank(detail.get("対象集合")) or not _nonblank(detail.get("根拠")):
            raise ValueError(f"範囲語 {marker!r} の対象集合か根拠が無い")
        line = detail.get("原稿行")
        if not isinstance(line, int) or isinstance(line, bool) or not 1 <= line <= len(lines) or not lines[line - 1].strip():
            raise ValueError(f"範囲語 {marker!r} の原稿行を特定できない")


def require(
    draft: str,
    *,
    body: str,
    entry_type: str,
    reviews: Sequence[str] = (),
    previous: str | None = None,
    updated: str | None = None,
) -> None:
    if entry_type != WI_TYPE_AWI:
        return
    if previous is None:
        if not _section(body, REQUEST_H2_QUOTATION):
            return
    else:
        assert updated is not None
        old_meta, old_body = frontmatter.parse_frontmatter(previous) or ({}, previous)
        new_meta, _ = frontmatter.parse_frontmatter(updated) or ({}, updated)
        if not (_origin(body) or _origin(old_body)):
            return
        if _reviewed_parts(body) == _reviewed_parts(old_body) and all(
            old_meta.get(key) == new_meta.get(key) for key in _TARGET_FIELDS
        ):
            return

    expected = _digest(draft)
    try:
        for path in reviews:
            record = json.loads(_absolute(path).read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("検収結果はJSON辞書で指定する")
            if record.get("原稿SHA256") == expected:
                _verify_record(record, frontmatter.normalize_newlines(draft), body)
                return
        raise ValueError("この版の原稿に対応する検収結果が無い")
    except (ValueError, OSError) as error:
        raise WebInputError(f"原要求の検収が不成立: {error}", next_action=_NEXT) from error
