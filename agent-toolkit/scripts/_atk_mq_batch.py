"""agent-toolkitプラグイン配下の`atk mq`一括取り込み用補助モジュール。

`atk mq show --all`の出力形式を入力として、複数エントリをinboxへ原文保持で取り込む。
別環境からの移行・復元用途であり、通常の投入経路（`_atk_mq_add.add_entries`）が行う
`target_commit`の再取得・TBD見出しと回答欄の再生成・予約frontmatterキーの破棄を適用しない。
取り込みが保存内容へ加える変更は次の2点だけとする。

1. 改行の正規化（CRLF・単独CRをLFへ揃え、`show`が付加する区切りの空行を除去して末尾改行1つへ揃える）。
2. 再採番が生じたエントリを参照する`depends_on`要素行の値の差し替え。

`show --all`の出力は可逆な直列化ではないため、次の限界がある（利用者確認済み）。

- 本文が完全な`show`形式エントリの引用を含む場合、エントリ境界を誤って分割し得る。
- 元ファイル末尾の改行の有無、末尾の連続空行、及び構造見出しと同形の末尾行は復元できない。

PEP 723 entrypoint`atk.py`と同一ディレクトリに配置され、`sys.path`挿入で相互import可能。
"""

import argparse
import collections
import dataclasses
import datetime
import functools
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import typing

import _atk_mq_frontmatter as _frontmatter
from _atk_mq_add import _body_is_effectively_empty, read_body_files
from _atk_mq_common import (
    MQ_STATE_INBOX,
    MQ_STATE_PROCESSING,
    MQ_STATES,
    MQ_TYPES,
    WebInputError,
    _collect_message_via_editor,
    _commit_and_push,
    _count_feedback,
    _max_existing_seq,
    _pull,
    _repo_lock,
    _subdir,
    validate_filename,
)
from _atk_mq_formatters import _shorten_home

_ENTRY_HEADING_RE = re.compile(r"### (?P<name>\S+\.md)(?: \[[^\]]*\])?")
"""エントリ境界となる`show`の見出し行。角括弧内の状態ラベルは無視する。"""

_TYPE_HEADING_RE = re.compile(r"# (?:feedback|tbd)")
_REPO_HEADING_RE = re.compile(r"## target_repo: .*")
_DEPENDS_ON_HEADING_RE = re.compile(r"depends_on:(?P<inline>.*)")
_DEPENDS_ON_ELEMENT_RE = re.compile(r"(?P<indent>[ \t]*)- (?P<value>.*)")
_DEPENDS_ON_VALUE_RE = re.compile(r"(?P<scalar>.*?)(?P<trailing>\s+#.*|\s*)")
"""`depends_on`の見出し行と要素行の値部分を、YAMLのプレーンスカラーと以降の空白・コメントへ分ける。

YAMLでは空白に続く`#`だけがコメントの開始となるため、最初の「空白＋`#`」以降と末尾の空白を
`trailing`へ分離する。分離結果が解析済みの依存先名と一致しない値（引用符付き等）は
境界を一意に特定できないものとして扱う。
"""


@dataclasses.dataclass(frozen=True)
class BatchEntry:
    """`show`形式から取り出した1エントリ。

    `raw_text`は末尾改行の正規化だけを適用した保存対象の全文とする。
    """

    original_name: str
    raw_text: str
    frontmatter: dict[str, typing.Any]
    body: str


def _is_structural_line(line: str) -> bool:
    """`show`が付加する構造見出しまたは空行かを判定する。"""
    return not line or _TYPE_HEADING_RE.fullmatch(line) is not None or _REPO_HEADING_RE.fullmatch(line) is not None


def _entry_boundaries(lines: list[str]) -> list[int]:
    """エントリ見出し行（直後がfrontmatter開始区切り）の行番号を返す。"""
    return [
        index
        for index, line in enumerate(lines)
        if _ENTRY_HEADING_RE.fullmatch(line) is not None and index + 1 < len(lines) and lines[index + 1] == "---"
    ]


def _validate_entry(name: str, raw_text: str) -> BatchEntry:
    """1エントリの保存前検証を行い、`BatchEntry`へ確定する。

    検証は解析結果の読み取りだけで完結させ、保存値は書き換えない。
    frontmatterの`target_repo`は非空の文字列であることのみを必須とし、
    リポジトリ識別子としての解決は要求しない（移行元にだけ存在する旧ローカルパス形式の
    保存値も取り込めるようにするため）。
    """
    parsed = _frontmatter.parse_frontmatter(raw_text)
    if parsed is None:
        raise WebInputError(f"frontmatterを解析できません: {name}")
    frontmatter, body = parsed
    entry_type = frontmatter.get("type")
    if entry_type not in MQ_TYPES:
        raise WebInputError(f"frontmatterのtypeがfeedback・tbdのいずれかではありません: {name}")
    target_repo = frontmatter.get("target_repo")
    if not isinstance(target_repo, str) or not target_repo.strip():
        raise WebInputError(f"frontmatterのtarget_repoを非空の文字列で指定してください: {name}")
    if entry_type == "feedback" and _body_is_effectively_empty(body):
        raise WebInputError(f"フィードバック本文が実質空です: {name}")
    return BatchEntry(original_name=name, raw_text=raw_text, frontmatter=frontmatter, body=body)


def parse_show_batch(text: str) -> list[BatchEntry]:
    """`atk mq show --all`の出力形式のテキストからエントリ列を取り出す。

    エントリ境界は`### <ファイル名>[ [状態]]`行のうち直後の行がfrontmatter開始区切りのものとする。
    先頭境界より前には空行と`show`の構造見出し（`# feedback`・`# tbd`・`## target_repo: ...`）だけを許し、
    それ以外を含む入力と境界が1件も無い入力は`WebInputError`で拒否する。
    各エントリの生テキストは境界の次行から次境界の前までとし、末尾の構造見出しと空行を除去してから
    末尾改行1つへ正規化する。frontmatterと本文はこの生テキストのまま保持する。
    行分割の前にCRLF・単独CRをLFへ正規化し、別環境（Windows等）から持ち込んだ入力でも
    境界行を検出できるようにする（保存内容の改行もLFへ揃う）。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    boundaries = _entry_boundaries(lines)
    if not boundaries:
        raise WebInputError("show形式のエントリ見出し（`### <ファイル名>`とその直後のfrontmatter）が見つかりません")
    for line in lines[: boundaries[0]]:
        if not _is_structural_line(line):
            raise WebInputError(f"show形式として解析できない行が先頭エントリより前にあります: {line}")
    entries: list[BatchEntry] = []
    for position, start in enumerate(boundaries):
        end = boundaries[position + 1] if position + 1 < len(boundaries) else len(lines)
        heading = _ENTRY_HEADING_RE.fullmatch(lines[start])
        assert heading is not None
        entry_lines = lines[start + 1 : end]
        while entry_lines and _is_structural_line(entry_lines[-1]):
            entry_lines.pop()
        entries.append(_validate_entry(heading.group("name"), "\n".join(entry_lines) + "\n"))
    return entries


def _existing_filenames(private_notes: pathlib.Path) -> set[str]:
    """4状態フォルダに実在する`.md`ファイル名の集合を返す。"""
    return {
        path.name
        for state in MQ_STATES
        if (private_notes / state).exists()
        for path in (private_notes / state).iterdir()
        if path.suffix == ".md"
    }


def _is_case_sensitive(directory: pathlib.Path) -> bool:
    """指定ディレクトリのファイルシステムがファイル名の大文字小文字を区別するかを実測する。

    OS種別から推定すると誤る（`os.path.normcase`はPOSIX実装では恒等関数であり、
    大文字小文字を区別しないファイルシステムを既定とする環境でも名前を畳み込まない）ため、
    一意な名前の空ファイルを当該ディレクトリへ作成し、名前の大文字小文字を反転させたパスが
    存在するかどうかで判定する。プローブ用ファイルは判定後に必ず削除する。
    """
    handle, created = tempfile.mkstemp(prefix=".atk-case-probe-", dir=directory)
    os.close(handle)
    probe = pathlib.Path(created)
    try:
        return not probe.with_name(probe.name.swapcase()).exists()
    finally:
        probe.unlink()


def _comparison_key(name: str, *, case_sensitive: bool) -> str:
    """ファイル名の衝突判定に用いる比較キーを返す。

    大文字小文字を区別しないファイルシステムでは同一物理パスへ解決される名前を同一視するため
    小文字化したキーを返し、区別するファイルシステムでは元の名前をそのまま返す。
    保存名自体はこのキーと分離し、常に元の大文字小文字を維持する。
    """
    return name if case_sensitive else name.lower()


def _assign_filenames(
    private_notes: pathlib.Path,
    entries: list[BatchEntry],
    *,
    existing: set[str],
    now: datetime.datetime,
    case_sensitive: bool,
) -> dict[str, str]:
    """元ファイル名から保存ファイル名への対応を書き込み前に一括確定する。

    元名を維持できるエントリを先に確定し、4状態フォルダの既存名と衝突するエントリだけを
    通常の投入経路と同じ採番規則で再採番する。再採番候補は既存名・元名を維持するエントリの元名・
    割り当て済みの保存名を予約集合として除外する。
    既存名との衝突判定と予約集合の判定は`_comparison_key`が返す比較キーで行い、
    大文字小文字を区別しないファイルシステムでも既存ファイルを上書きしない。
    """
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    key = functools.partial(_comparison_key, case_sensitive=case_sensitive)
    reserved = {key(name) for name in existing}
    assignments = {entry.original_name: entry.original_name for entry in entries if key(entry.original_name) not in reserved}
    reserved |= {key(name) for name in assignments}
    counter = _max_existing_seq(private_notes, timestamp) + 1
    for entry in entries:
        if entry.original_name in assignments:
            continue
        candidate = f"{timestamp}-{counter:03d}.md"
        while key(candidate) in reserved:
            counter += 1
            candidate = f"{timestamp}-{counter:03d}.md"
        assignments[entry.original_name] = candidate
        reserved.add(key(candidate))
        counter += 1
    return assignments


def _has_inline_value(inline: str) -> bool:
    """`depends_on:`見出し行の`:`以降に、YAMLコメントを除いた値が書かれているかを判定する。"""
    split = _DEPENDS_ON_VALUE_RE.fullmatch(inline)
    assert split is not None
    return bool(split.group("scalar").strip())


def _rewrite_depends_on(entry: BatchEntry, renames: dict[str, str]) -> str:
    """再採番された元ファイル名を参照する`depends_on`要素行だけを差し替える。

    差し替えはfrontmatter内の`depends_on`ブロック（行頭`depends_on:`行と直後に続く
    シーケンス要素行）に限定し、他のキー・コメント・本文の字面を保持する。
    差し替えは要素行の値部分だけを対象とし、値より後ろの空白・コメントは字面ごと残す。
    `depends_on`は本CLIの`serialize_frontmatter`が生成するブロック形式シーケンスを正準形とし、
    flow形式など読み替え位置を行単位で確定できない形式と、引用符付きなど値とコメントの境界を
    一意に特定できない要素行は`WebInputError`で拒否する。
    見出し行末尾のYAMLコメントは値と見なさず、後続の要素行をブロックとして受理する。
    """
    raw_dependencies = entry.frontmatter.get("depends_on")
    dependencies: list[typing.Any] = raw_dependencies if isinstance(raw_dependencies, list) else []
    renamed = [value for value in dependencies if isinstance(value, str) and value in renames]
    if not renamed:
        if isinstance(raw_dependencies, str) and raw_dependencies in renames:
            raise WebInputError(f"depends_onがブロック形式シーケンスではないため読み替えできません: {entry.original_name}")
        return entry.raw_text
    lines = entry.raw_text.split("\n")
    frontmatter_end = lines.index("---", 1)
    heading_index: int | None = None
    for index in range(1, frontmatter_end):
        heading = _DEPENDS_ON_HEADING_RE.fullmatch(lines[index])
        if heading is None:
            continue
        if _has_inline_value(heading.group("inline")):
            raise WebInputError(f"depends_onがブロック形式シーケンスではないため読み替えできません: {entry.original_name}")
        heading_index = index
        break
    if heading_index is None:
        raise WebInputError(f"depends_onのブロックを特定できないため読み替えできません: {entry.original_name}")
    elements: list[tuple[int, str, str]] = []
    for index in range(heading_index + 1, frontmatter_end):
        element = _DEPENDS_ON_ELEMENT_RE.fullmatch(lines[index])
        if element is None:
            break
        elements.append((index, element.group("indent"), element.group("value")))
    block = "---\n" + "\n".join(lines[heading_index : heading_index + 1 + len(elements)]) + "\n---\n"
    parsed_block = _frontmatter.parse_frontmatter(block)
    if parsed_block is None or parsed_block[0].get("depends_on") != dependencies:
        raise WebInputError(f"depends_onの要素行を一意に特定できないため読み替えできません: {entry.original_name}")
    for (index, indent, value), dependency in zip(elements, dependencies, strict=True):
        if not isinstance(dependency, str) or dependency not in renames:
            continue
        split = _DEPENDS_ON_VALUE_RE.fullmatch(value)
        assert split is not None
        if split.group("scalar") != dependency:
            raise WebInputError(
                f"depends_onの要素行で値とコメントの境界を特定できないため読み替えできません: {entry.original_name}（{value}）"
            )
        # 差し替え先は本モジュールが採番した`{タイムスタンプ}-{連番}.md`形式であり、
        # 引用符を必要としないYAMLのプレーンスカラーに該当する。値以降の空白とコメントは字面ごと残す。
        lines[index] = f"{indent}- {renames[dependency]}{split.group('trailing')}"
    return "\n".join(lines)


def _declared_dependencies(entry: BatchEntry) -> list[str]:
    """`depends_on`が宣言する依存先名を列として返す。

    ブロック形式・flow形式のシーケンスに加え、`_rewrite_depends_on`が読み替え不要時に受理する
    スカラー（文字列1件）形式も1要素の依存として扱う。
    """
    declared = entry.frontmatter.get("depends_on")
    if isinstance(declared, str):
        return [declared]
    if isinstance(declared, list):
        return [value for value in declared if isinstance(value, str)]
    return []


def _dependency_warnings(
    entries: list[BatchEntry],
    *,
    assignments: dict[str, str],
    existing: set[str],
    case_sensitive: bool,
) -> list[str]:
    """取り込み先に実在しない`depends_on`参照を警告文へ列挙する。

    取り込み後に実在する名前は、4状態フォルダの既存名、バッチ内エントリの元名
    （再採番された元名への参照は`_rewrite_depends_on`が新名へ差し替える）、
    及び再採番で確定した保存名の3種とする。
    判定対象の依存先は`_declared_dependencies`が返す列とし、スカラー形式の`depends_on`も含める。
    実在判定は`_comparison_key`が返す比較キーで行い、大文字小文字を区別しないファイルシステムで
    大小の綴りだけが異なる参照を不在と誤判定しない。警告文には参照の原文を用いる。
    """
    warnings: list[str] = []
    key = functools.partial(_comparison_key, case_sensitive=case_sensitive)
    resolvable = {key(name) for name in assignments} | {key(name) for name in assignments.values()}
    resolvable |= {key(name) for name in existing}
    for entry in entries:
        for dependency in _declared_dependencies(entry):
            if key(dependency) in resolvable:
                continue
            warnings.append(f"{assignments[entry.original_name]}のdepends_onが参照する{dependency}は取り込み先に実在しません")
    return warnings


def add_batch_entries(
    private_notes: pathlib.Path,
    *,
    texts: list[str],
    now: datetime.datetime,
    lock_timeout: float = -1,
) -> tuple[list[tuple[str, str]], list[str]]:
    """`show`形式のテキスト群を解析し、全エントリをinboxへ原文保持で取り込む。

    戻り値は`(元ファイル名, 保存ファイル名)`の対応リストと警告リストとする。
    元ファイル名は連結後の全体集合で重複を検査し、重複があれば`depends_on`の読み替え先が
    一意に定まらないため全件拒否する。
    重複の検査と既存名との衝突判定は、取り込み先ディレクトリの大文字小文字の区別を
    `_is_case_sensitive`で実測した結果に基づく比較キーで行い、大文字小文字を区別しない
    ファイルシステムで書き込みが互いを上書きする組も拒否する。
    ファイル名は取り込み先と衝突しない限り元名を維持する。
    """
    if not texts:
        raise WebInputError("取り込む本文を1件以上指定してください")
    entries = [entry for text in texts for entry in parse_show_batch(text)]
    inbox_dir = _subdir(private_notes, MQ_STATE_INBOX)
    for entry in entries:
        validate_filename(entry.original_name, inbox_dir)
    with _repo_lock(private_notes, timeout=lock_timeout):
        _pull(private_notes)
        case_sensitive = _is_case_sensitive(inbox_dir)
        counts = collections.Counter(_comparison_key(entry.original_name, case_sensitive=case_sensitive) for entry in entries)
        duplicated = sorted(
            {
                entry.original_name
                for entry in entries
                if counts[_comparison_key(entry.original_name, case_sensitive=case_sensitive)] > 1
            }
        )
        if duplicated:
            raise WebInputError(f"元ファイル名が重複しています: {'、'.join(duplicated)}")
        existing = _existing_filenames(private_notes)
        assignments = _assign_filenames(
            private_notes,
            entries,
            existing=existing,
            now=now,
            case_sensitive=case_sensitive,
        )
        renames = {original: saved for original, saved in assignments.items() if original != saved}
        contents = [(assignments[entry.original_name], _rewrite_depends_on(entry, renames)) for entry in entries]
        for filename, content in contents:
            (inbox_dir / filename).write_text(content, encoding="utf-8")
        warnings = _dependency_warnings(
            entries,
            assignments=assignments,
            existing=existing,
            case_sensitive=case_sensitive,
        )
        count = len(entries)
        _commit_and_push(
            private_notes,
            f"chore: add {count} imported {'item' if count == 1 else 'items'}",
            [MQ_STATE_INBOX],
        )
    return [(entry.original_name, assignments[entry.original_name]) for entry in entries], warnings


def _collect_batch_texts(args: argparse.Namespace) -> list[str]:
    """一括取り込みの入力テキスト群を確定する。

    エディター経路は原文保持のため`strip`を適用しない収集形（`strip=False`）を用いる。
    """
    body_files = getattr(args, "body_file", None)
    if args.messages:
        if body_files:
            args.subparser.error("--body-fileとMESSAGE位置引数は併用できません")
        return list(args.messages)
    if body_files:
        try:
            return read_body_files(body_files)
        except WebInputError as error:
            print(f"投入を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
    text = _collect_message_via_editor(strip=False)
    if text is None:
        sys.exit(1)
    return [text]


def _cmd_add_batch(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    now: datetime.datetime,
    home: pathlib.Path,
) -> None:
    """`mq add --batch`: `show`形式のテキストを一括でinboxへ取り込む。

    入力はMESSAGE位置引数群、`--body-file`群、$EDITORのいずれか1経路から収集する。
    remote同期失敗時は確定済みの入力をstderrへ再表示し、内容の消失を防ぐ。
    """
    texts = _collect_batch_texts(args)
    try:
        mapping, warnings = add_batch_entries(private_notes, texts=texts, now=now)
    except WebInputError as error:
        print(f"投入を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError:
        print("remote同期に失敗しました。確定済みの本文が消失しないよう以下に再表示します。", file=sys.stderr)
        for text in texts:
            print("---", file=sys.stderr)
            print(text, file=sys.stderr)
        sys.exit(1)
    inbox_dir = _subdir(private_notes, MQ_STATE_INBOX)
    processing_dir = _subdir(private_notes, MQ_STATE_PROCESSING)
    print(f"{len(mapping)}件取り込み:")
    for original, saved in mapping:
        renamed = f"（{original} -> {saved}）" if original != saved else ""
        print(f"  {_shorten_home(inbox_dir / saved, home)}{renamed}")
    for warning in warnings:
        print(f"警告: {warning}", file=sys.stderr)
    print(f"inbox: 計{_count_feedback(inbox_dir)}件（processing: {_count_feedback(processing_dir)}件）")
