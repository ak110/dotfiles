"""agent-toolkit参照の主題別不変条件を検証する。"""

from __future__ import annotations

import pathlib
import re

import pytest

from _test_helpers import _AUDIT_RECORDS, _SOURCE_SUFFIXES, _tracked_source_paths

# 監査記録の見出しを、索引元の条文またはコードが同じ文字列で指すことを検査する。
# 索引元は、テスト（`*_test.py`）と`docs/`配下を除く追跡ファイルと、見出しのファイル部分が指すファイル自身とする。
# テスト、設計記録と判定台帳は見出しを索引元として持たずに引用するため、そこにだけ現れる見出しは参照元を持たない。
# 常時規範（`agent-toolkit/share/rules/common/`配下と`agent-toolkit/share/rules/rules-*.md`）は全主体が毎回読む。
# そのため条文へ索引の文を置かず、見出しそのものを索引とする。
# ファイル部分が常時規範を指す見出しは、そのファイルに節名と同じ見出しがあることで参照ありとする。
# 規範原本を`share/rules/`へ集める前の見出しは旧パスのまま当時の記録として残すため、現在の所在へ読み替えて照合する。
_RULE_DOCUMENT_RELOCATIONS = (
    ("agent-toolkit/rules/", "agent-toolkit/share/rules/common/"),
    ("agent-toolkit/share/rules-", "agent-toolkit/share/rules/rules-"),
)


_AUDIT_HEADING_PATTERN = re.compile(r"^## (?P<heading>.+)$", re.MULTILINE)


_AUDIT_INDEX_EXCLUDED_PREFIX = "docs/"


# 監査記録の本文が他の記録を「<対象>：<節>：<年月日>」の見出しで指す参照。
_AUDIT_BODY_REFERENCE_PATTERN = re.compile(r"「(?P<heading>[^」\n]*：[^」\n]*\d{4}年\d{1,2}月[^」\n]*)」")


def _comment_joined(content: str) -> str:
    """行頭の空白とコメント記号`#`を除いて各行を連結し、コメントの改行で分かれた参照を1つの文字列にする。"""
    return "".join(line.strip().lstrip("#").strip() for line in content.splitlines())


def _audit_headings(content: str) -> list[str]:
    """監査記録のH2見出しを出現順に返す。"""
    return [match.group("heading") for match in _AUDIT_HEADING_PATTERN.finditer(content)]


def _reference_text(root: pathlib.Path, source: pathlib.Path) -> str:
    """見出しの参照を探す本文として、ファイルの本文とコメントの改行を連結した本文を返す。"""
    content = (root / source).read_text(encoding="utf-8")
    return f"{content}\n{_comment_joined(content)}"


def _current_rule_path(path: pathlib.Path) -> pathlib.Path:
    """旧配置の規範パスを現在の所在へ読み替える。"""
    value = path.as_posix()
    for old, new in _RULE_DOCUMENT_RELOCATIONS:
        if value.startswith(old):
            return pathlib.Path(new + value.removeprefix(old))
    return path


def _is_rule_document(path: pathlib.Path) -> bool:
    """常時規範（`agent-toolkit/share/rules/common/`直下と`agent-toolkit/share/rules/rules-*.md`）のパスかを返す。旧配置も読み替えて判定する。"""
    path = _current_rule_path(path)
    if path.suffix != ".md":
        return False
    parent = path.parent.as_posix()
    return parent == "agent-toolkit/share/rules/common" or (
        parent == "agent-toolkit/share/rules" and path.name.startswith("rules-")
    )


def _rule_section_exists(root: pathlib.Path, tracked: set[pathlib.Path], heading: str) -> bool:
    """常時規範を指す監査見出しについて、ファイルが実在しそのファイルに節名と同じ見出しがあるかを返す。"""
    parts = heading.split("：")
    if len(parts) < 3:
        return False
    rule_file = _current_rule_path(pathlib.Path(parts[0]))
    section = "：".join(parts[1:-1])
    if rule_file not in tracked:
        return False
    content = (root / rule_file).read_text(encoding="utf-8")
    return re.search(rf"^#{{2,6}} {re.escape(section)}$", content, re.MULTILINE) is not None


def _audit_record_content(root: pathlib.Path) -> str:
    """主題別に分割された監査記録を、見出しと相互参照の共通検査へ渡す。"""
    paths = sorted((root / _AUDIT_RECORDS.parent).glob("audit-records*.md"))
    assert paths, "監査記録の検査対象が無い"
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def _unreferenced_audit_headings(root: pathlib.Path, sources: list[pathlib.Path]) -> list[str]:
    """監査記録のH2見出しのうち参照元を持たないものを返す。

    常時規範を指す見出しはファイルと節の実在で判定し、他の見出しは索引元の追跡ファイルか
    見出しのファイル部分が指すファイル自身に現れるかで判定する。
    """
    headings = _audit_headings(_audit_record_content(root))
    corpus = "\n".join(
        _reference_text(root, source)
        for source in sources
        if not source.name.endswith("_test.py") and not source.as_posix().startswith(_AUDIT_INDEX_EXCLUDED_PREFIX)
    )
    tracked = set(sources) - {_AUDIT_RECORDS}
    unreferenced: list[str] = []
    for heading in headings:
        own_file = pathlib.Path(heading.split("：", 1)[0])
        if _is_rule_document(own_file):
            if not _rule_section_exists(root, tracked, heading):
                unreferenced.append(heading)
            continue
        if heading in corpus:
            continue
        if own_file in tracked and heading in _reference_text(root, own_file):
            continue
        unreferenced.append(heading)
    return unreferenced


def _unresolved_audit_body_references(content: str) -> list[str]:
    """監査記録の本文で、実在しない見出しを「」で指す参照を出現順に返す。"""
    headings = set(_audit_headings(content))
    body = _AUDIT_HEADING_PATTERN.sub("", content)
    return [
        match.group("heading")
        for match in _AUDIT_BODY_REFERENCE_PATTERN.finditer(body)
        if match.group("heading") not in headings
    ]


def test_audit_record_headings_are_referenced() -> None:
    """監査記録の各H2見出しを、索引元の条文またはコードが同じ文字列で指す。"""
    root = pathlib.Path(__file__).resolve().parent
    unreferenced = _unreferenced_audit_headings(root, _tracked_source_paths(root, _SOURCE_SUFFIXES | {".ts", ".tsx"}))
    assert not unreferenced, (
        "参照元を持たない監査記録の見出し（常時規範を指す見出しは条文へ索引の文を置かず、見出しのファイルと節名を条文の現在の所在へ直す。"
        "それ以外は索引元へ索引の1文を置くか、索引先の無い記録を削除する）:\n" + "\n".join(unreferenced)
    )


def test_audit_record_body_heading_references_resolve() -> None:
    """監査記録の本文が「」で指す他の記録の見出しが、監査記録に実在する。"""
    root = pathlib.Path(__file__).resolve().parent
    unresolved = _unresolved_audit_body_references(_audit_record_content(root))
    assert not unresolved, (
        "実在しない見出しを指す監査記録の本文の参照（参照先の記録の観測と再検証の手順を参照元の本文へ統合するか、見出しを直す）:\n"
        + "\n".join(unresolved)
    )


@pytest.mark.parametrize("split_records", [False, True])
def test_unreferenced_audit_heading_detector_joins_wrapped_comments(tmp_path: pathlib.Path, split_records: bool) -> None:
    """コメントの改行で分かれた参照を参照ありとし、どこにも現れない見出しだけを報告する。"""
    audit = tmp_path / _AUDIT_RECORDS
    audit.parent.mkdir(parents=True)
    audit.write_text(
        "# 監査記録\n\n## a.py：節：2026年1月1日\n\n記録。\n\n## b.md：節：2026年1月2日\n\n記録。\n", encoding="utf-8"
    )
    source = pathlib.Path("a.py")
    (tmp_path / source).write_text(
        "# 監査記録は`docs/development/audit-records.md`の\n# 「a.py：節：2026年1月1日」にある。\n", encoding="utf-8"
    )
    ledger = pathlib.Path("docs/development/norm-restructure/ledger.md")
    (tmp_path / ledger).parent.mkdir(parents=True)
    (tmp_path / ledger).write_text("b.md：節：2026年1月2日\n", encoding="utf-8")

    sources = [_AUDIT_RECORDS, source, ledger]
    if split_records:
        content = audit.read_text(encoding="utf-8")
        first, second = content.split("## b.md", 1)
        audit.write_text(first, encoding="utf-8")
        other = audit.with_name("audit-records-other.md")
        other.write_text("# 別の主題\n\n## b.md" + second, encoding="utf-8")
        sources.append(other.relative_to(tmp_path))

    assert _unreferenced_audit_headings(tmp_path, sources) == ["b.md：節：2026年1月2日"]


def test_unreferenced_audit_heading_detector_ignores_tests_and_docs(tmp_path: pathlib.Path) -> None:
    """テストと`docs/`配下の他のファイルだけに現れる見出しを報告し、コードか見出しのファイル自身に現れる見出しを参照ありとする。"""
    in_code = "tool.py：節：2026年1月1日"
    in_own_file = "docs/development/design-a.md：節：2026年1月2日"
    only_tests_and_docs = "c.md：節：2026年1月3日"
    only_other_doc = "docs/development/design-b.md：節：2026年1月4日"
    audit = tmp_path / _AUDIT_RECORDS
    audit.parent.mkdir(parents=True)
    audit.write_text(
        "# 監査記録\n\n"
        + "".join(f"## {heading}\n\n記録。\n\n" for heading in (in_code, in_own_file, only_tests_and_docs, only_other_doc)),
        encoding="utf-8",
    )
    files = {
        pathlib.Path("tool.py"): f"# 監査記録は「{in_code}」にある。\n",
        pathlib.Path("sample_test.py"): f"# 「{only_tests_and_docs}」\n",
        pathlib.Path("docs/development/design-a.md"): f"「{in_own_file}」「{only_tests_and_docs}」「{only_other_doc}」\n",
        pathlib.Path("docs/development/design-b.md"): "# 設計\n",
    }
    for path, content in files.items():
        (tmp_path / path).write_text(content, encoding="utf-8")

    assert _unreferenced_audit_headings(tmp_path, [_AUDIT_RECORDS, *files]) == [only_tests_and_docs, only_other_doc]


def test_rule_audit_heading_requires_existing_section(tmp_path: pathlib.Path) -> None:
    """常時規範を指す見出しは、索引の文の有無にかかわらず、ファイルに同名の節がある場合だけ参照ありとする。"""
    existing = "agent-toolkit/rules/01-a.md：節A：2026年1月1日"
    renamed = "agent-toolkit/rules/01-a.md：消えた節：2026年1月2日"
    share_existing = "agent-toolkit/share/rules-main.md：節B：2026年1月3日"
    missing_file = "agent-toolkit/share/rules-gone.md：節C：2026年1月4日"
    audit = tmp_path / _AUDIT_RECORDS
    audit.parent.mkdir(parents=True)
    audit.write_text(
        "# 監査記録\n\n"
        + "".join(f"## {heading}\n\n記録。\n\n" for heading in (existing, renamed, share_existing, missing_file)),
        encoding="utf-8",
    )
    files = {
        pathlib.Path("agent-toolkit/share/rules/common/01-a.md"): "# 規範\n\n## 上位\n\n### 節A\n\n本文。\n",
        pathlib.Path("agent-toolkit/share/rules/rules-main.md"): "# 規範\n\n## 節B\n\n本文。\n",
        pathlib.Path("tool.py"): f"# 監査記録は「{renamed}」と「{missing_file}」にある。\n",
    }
    for path, content in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content, encoding="utf-8")

    assert _unreferenced_audit_headings(tmp_path, [_AUDIT_RECORDS, *files]) == [renamed, missing_file]


@pytest.mark.parametrize("split_records", [False, True])
def test_audit_body_reference_to_missing_heading_is_reported(tmp_path: pathlib.Path, split_records: bool) -> None:
    """監査記録の本文が実在しない見出しを指すと報告し、実在する見出しと見出し以外の「」は報告しない。"""
    content = (
        "# 監査記録\n\n"
        "## a.py：節：2026年1月1日\n\n"
        "根拠は「b.md：待機区間の構成：2026年9月」にある。\n"
        "閾値は「a.py：節：2026年1月1日」と「判断指針」を参照する。\n\n"
        "## b.md：待機区間の構成：2026年9月3日\n\n記録。\n"
    )

    audit = tmp_path / _AUDIT_RECORDS
    audit.parent.mkdir(parents=True)
    if split_records:
        first, second = content.split("## b.md", 1)
        audit.write_text(first, encoding="utf-8")
        audit.with_name("audit-records-other.md").write_text("# 別の主題\n\n## b.md" + second, encoding="utf-8")
    else:
        audit.write_text(content, encoding="utf-8")

    assert _unresolved_audit_body_references(_audit_record_content(tmp_path)) == ["b.md：待機区間の構成：2026年9月"]
