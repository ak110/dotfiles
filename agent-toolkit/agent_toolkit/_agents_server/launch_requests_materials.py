"""レビューと探索の起動に必要な保存資料の検査。"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server.launch_requests_preflight import PREFLIGHT_TIMEOUT
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._atk.wi import frontmatter, headings
from agent_toolkit._atk.wi.constants import normalized_wi_type
from agent_toolkit._common import private_notes, task_documents
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import command as git_command
from agent_toolkit._plan import commit_mapping, review_contract


def _material_error(name: str, reason: str) -> ActionableError:
    """不足資料と、保存して再発行する操作を同じ応答へ載せる。"""
    return ActionableError(
        f"探索資料の入力が不成立です: {name}: {reason}。委譲先は起動していない。",
        next_action=(
            f"`{name}`に必要な本文・差分・証拠をUTF-8の読取可能な通常ファイルへ保存し、"
            "絶対パスで補完して`start`を再発行する。reader-fit-reviewの`比較資料`は、Git管理下では"
            '`{"repository":"/repo","base":"<比較元OID>","head":"<レビューHEAD>","paths":["文書.md"]}`とし、'
            "担当が`git show`・`git diff`で読む。Git管理外では"
            '`{"比較元本文":"/保存先/旧本文.md","今回差分":"/保存先/差分.patch","現行本文":"/成果物.md"}`'
            "とし、複数成果物では同じ3項目のオブジェクト配列を渡す。"
            "新規成果物だけ比較元本文へ`新規`、今回差分へ`全文`を指定できる。"
            "再レビューの`未解決事項`は保存ファイルの絶対パス、その絶対パスのJSON配列か`なし`だけを渡し、説明は保存物へ書く"
        ),
    )


def _material_paths(name: str, value: str) -> list[pathlib.Path]:
    """JSON配列と既存の保存ファイル指定を、共通の読取検査へ渡す。"""
    value = value.strip()
    if value.startswith("["):
        try:
            items = json.loads(value)
        except json.JSONDecodeError as error:
            raise _material_error(name, "パス配列のJSONを読めない") from error
        if not isinstance(items, list) or not items or any(not isinstance(item, str) for item in items):
            raise _material_error(name, "絶対パスの空でないJSON配列が必要")
    else:
        # 全体が実在するパスの場合は、空白・日本語の区切り文字を含む名前もそのまま保つ。
        if pathlib.Path(value).is_absolute() and pathlib.Path(value).is_file():
            items = [value]
        else:
            tokens = re.findall(r"`(/[^`]+)`|(/[^\s`、,;；（）()]+)", value)
            items = [quoted or bare for quoted, bare in tokens]
    if not items:
        raise _material_error(name, "保存ファイルの絶対パスが無い")
    paths = [pathlib.Path(item) for item in items]
    errors: list[str] = []
    for path in paths:
        if not path.is_absolute() or not path.is_file():
            errors.append(f"通常ファイルを参照できない: {path}")
            continue
        try:
            with path.open(encoding="utf-8") as stream:
                for _line in stream:
                    pass
        except (OSError, UnicodeError) as error:
            errors.append(f"UTF-8で読めない: {path}: {error}")
    if errors:
        raise _material_error(name, "\n".join(errors))
    return paths


def _check_reader_unresolved(params: Mapping[str, str], review_kind: str) -> None:
    """再レビューで必要な未解決事項の保存物を、パスだけの値として確かめる。

    パスに説明を連結した値は、説明の対象が別の成果物でも探索担当が今回の資料の範囲と誤認するため受理しない。
    """
    unresolved = params.get("未解決事項", "なし" if review_kind == "初回レビュー" else "").strip()
    if unresolved == "なし":
        return
    if not unresolved.startswith("[") and not (pathlib.Path(unresolved).is_absolute() and pathlib.Path(unresolved).is_file()):
        raise _material_error(
            "未解決事項", "値は保存ファイルの絶対パス、その絶対パスのJSON配列か`なし`だけにし、説明は保存物へ書く"
        )
    _material_paths("未解決事項", unresolved)


_HUNK_HEADER = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _apply_unified_diff(before: str, patch: str) -> str | None:
    """1ファイル分の統一差分を比較元本文へ適用した本文を返し、適用できない差分ではNoneを返す。

    行位置を持たない帰属限定差分、複数ファイルの差分、比較元と文脈が一致しない差分は適用できない。
    """
    lines = patch.splitlines(keepends=True)
    if sum(1 for line in lines if line.startswith("+++ ")) > 1:
        return None
    entries: list[tuple[str, str]] = []
    for line in lines:
        if line.startswith("\\"):
            if not entries:
                return None
            tag, text = entries[-1]
            entries[-1] = (tag, text.removesuffix("\n"))
        else:
            entries.append((line[:1], line[1:]))
    source = before.splitlines(keepends=True)
    result: list[str] = []
    position = 0
    index = 0
    while index < len(entries) and not (entries[index][0] == "@" and entries[index][1].startswith("@ ")):
        if entries[index][0] == "@":
            return None
        index += 1
    while index < len(entries):
        header = _HUNK_HEADER.match("@" + entries[index][1])
        if header is None:
            return None
        old_start, old_count = int(header[1]), int(header[2] if header[2] is not None else 1)
        new_count = int(header[4] if header[4] is not None else 1)
        anchor = old_start if old_count == 0 else old_start - 1
        if anchor < position or anchor > len(source):
            return None
        result.extend(source[position:anchor])
        position = anchor
        index += 1
        while old_count or new_count:
            if index >= len(entries):
                return None
            tag, text = entries[index]
            if tag in {" ", "-"}:
                if position >= len(source) or source[position] != text or old_count == 0:
                    return None
                position += 1
                old_count -= 1
            if tag in {" ", "+"}:
                if new_count == 0:
                    return None
                result.append(text)
                new_count -= 1
            if tag not in {" ", "-", "+"}:
                return None
            index += 1
    result.extend(source[position:])
    return "".join(result)


def _patch_target_error(patch: str, current: pathlib.Path) -> str | None:
    """差分の新しい側のファイル見出しが現行本文と異なるファイルを指す場合に理由を返す。"""
    for line in patch.splitlines():
        if not line.startswith("+++ "):
            continue
        target = line[4:].split("\t", 1)[0].strip()
        if target == "/dev/null":
            return None
        relative = target.removeprefix("b/") if not pathlib.Path(target).is_absolute() else target
        if pathlib.Path(relative).is_absolute():
            matched = pathlib.Path(relative) == current
        else:
            matched = current.as_posix().endswith("/" + relative.lstrip("/"))
        return None if matched else f"差分の対象`{target}`が現行本文`{current}`と異なる成果物を指す"
    return None


def _check_patch_consistency(name: str, before: str, patch_path: pathlib.Path, current: pathlib.Path) -> None:
    """比較元本文へ今回差分を適用した結果が現行本文と一致するかを確かめる。

    別成果物の差分、計画別の判定範囲だけの差分、レビューHEADと異なる現行本文は、いずれもこの一致が崩れる。
    """
    patch = patch_path.read_text(encoding="utf-8")
    if error := _patch_target_error(patch, current):
        raise _material_error(name, error)
    expected = current.read_text(encoding="utf-8")
    applied = _apply_unified_diff(before, patch) if patch else before
    if applied != expected:
        raise _material_error(
            name,
            "比較元本文へ今回差分を適用した結果が現行本文と一致しない。今回差分は比較元と現行本文の本文間の完全な差分にし、"
            "計画別の差分は`判定範囲差分`へ分ける。現行本文はレビューHEADの同じ版を渡す",
        )


def _check_reader_materials(params: Mapping[str, str]) -> None:
    """現行本文・比較元・今回差分を区別し、再レビューの証拠も起動前に読む。"""
    current = _material_paths("成果物", params["成果物"])
    if len(set(current)) != len(current):
        raise _material_error("成果物", "現行本文のパスが重複している")
    review_kind = params.get("レビュー種別", "初回レビュー")
    if review_kind not in {"初回レビュー", "再レビュー", "引き継ぎ再レビュー"}:
        raise _material_error("レビュー種別", "初回レビュー・再レビュー・引き継ぎ再レビューのいずれかが必要")
    if "比較資料" in params:
        try:
            materials = json.loads(params["比較資料"])
        except json.JSONDecodeError as error:
            raise _material_error("比較資料", "JSONオブジェクトまたはその配列を読めない") from error
        if isinstance(materials, dict) and set(materials) - {"判定範囲差分"} == {"repository", "base", "head", "paths"}:
            _check_git_reader_materials(materials, current)
            _check_reader_unresolved(params, review_kind)
            return
        if isinstance(materials, dict) and len(current) == 1:
            materials = [materials]
        if not isinstance(materials, list) or not materials:
            raise _material_error("比較資料", "成果物ごとの比較資料の空でない配列が必要")
        keys = {"比較元本文", "今回差分", "現行本文"}
        covered: set[pathlib.Path] = set()
        for material in materials:
            if (
                not isinstance(material, dict)
                or set(material) - {"判定範囲差分"} != keys
                or any(not isinstance(value, str) for value in material.values())
            ):
                raise _material_error("比較資料", "比較元本文・今回差分・現行本文の3項目（任意で判定範囲差分）が必要")
            actual = _material_paths("現行本文", material["現行本文"])
            if len(actual) != 1 or actual[0] not in current:
                raise _material_error("現行本文", "成果物のいずれか1件と同じパスが必要")
            if actual[0] in covered:
                raise _material_error("比較資料", f"現行本文の対応が重複している: {actual[0]}")
            covered.add(actual[0])
            is_new = material["比較元本文"] == "新規"
            if review_kind != "初回レビュー" and is_new:
                raise _material_error("比較元本文", "再レビューでは前回確認版の本文が必要")
            before = ""
            if not is_new:
                before = _material_paths("比較元本文", material["比較元本文"])[0].read_text(encoding="utf-8")
            if not (is_new and material["今回差分"] == "全文"):
                patch_paths = _material_paths("今回差分", material["今回差分"])
                if len(patch_paths) != 1:
                    raise _material_error("今回差分", "成果物ごとに1件の差分ファイルが必要")
                _check_patch_consistency("今回差分", before, patch_paths[0], actual[0])
            if "判定範囲差分" in material:
                scope_paths = _material_paths("判定範囲差分", material["判定範囲差分"])
                if len(scope_paths) != 1:
                    raise _material_error("判定範囲差分", "成果物ごとに1件の差分ファイルが必要")
                if error := _patch_target_error(scope_paths[0].read_text(encoding="utf-8"), actual[0]):
                    raise _material_error("判定範囲差分", error)
        if covered != set(current):
            raise _material_error("比較資料", "全成果物に比較資料を1件ずつ対応付ける必要がある")
    else:
        if len(current) != 1:
            raise _material_error("比較資料", "複数成果物には比較資料の配列が必要")
        # 旧形式も保存ファイルを共通検査へ通す。本文や差分の説明だけでは成立しない。
        scope = params["修正範囲"]
        is_new = scope.startswith("新規") and "全文" in scope
        if not is_new:
            paths = _material_paths("修正範囲（比較元本文・今回差分）", scope)
            saved = list(dict.fromkeys(path for path in paths if path != current[0]))
            if len(saved) < 2:
                raise _material_error("比較資料", "比較元本文と今回差分の保存物が必要")
            # 旧形式は役割を名前で示さないため、差分の形を持つファイルを1件に識別できる場合だけ同じ対応を確かめる。
            patches = [path for path in saved if path.read_text(encoding="utf-8").startswith(("diff --git ", "--- "))]
            if len(patches) == 1 and len(saved) == 2:
                before_path = next(path for path in saved if path != patches[0])
                _check_patch_consistency(
                    "修正範囲（比較元本文・今回差分）", before_path.read_text(encoding="utf-8"), patches[0], current[0]
                )
        if review_kind != "初回レビュー" and is_new:
            raise _material_error("比較元本文", "再レビューでは前回確認版の本文が必要")
    _check_reader_unresolved(params, review_kind)


def _check_explore_materials(task_document: pathlib.Path, params: Mapping[str, str]) -> None:
    """読取と検索だけで動く担当の保存資料を、起動要求の境界で検査する。"""
    role = task_document.name.removesuffix(task_documents.TASK_DOCUMENT_SUFFIX)
    if role == "wi-draft-review":
        stage = params.get("確認段階", "本文")
        if stage not in {"方針", "本文"}:
            raise _material_error("確認段階", "方針または本文を指定する。省略時は本文")
        name = "方針" if stage == "方針" else "原稿"
        if not params.get(name):
            raise _material_error(name, f"{stage}段階で必要")
        for material in (name, "要求と出所", "成立性の証拠"):
            path = pathlib.Path(params[material])
            if not path.is_absolute() or not path.is_file():
                raise _material_error(material, "実在するファイルの絶対パスが必要")
            _material_paths(material, params[material])
        return
    if role == "exec-review":
        _check_exec_review_materials(params)
        return
    if role == "reader-fit-review":
        _check_reader_materials(params)
        return
    required_files = {
        "external-write-review": ("文面ファイル",),
        "bulk-replace-review": ("差分ファイル",),
    }
    for name in required_files.get(role, ()):
        _material_paths(name, params[name])
    if role == "external-write-review":
        evidence = params["根拠の所在"].strip()
        if evidence.startswith(("/", "`/", "[")) or pathlib.PureWindowsPath(evidence).is_absolute():
            _material_paths("根拠の所在", evidence)


_REVIEW_BASES = ("計画", "CI記録", "WI")

_REVIEW_BASIS_INPUTS: dict[str, tuple[str, ...]] = {
    "計画": ("計画",),
    "CI記録": ("CI記録", "修正系列の開始時のHEAD", "原因commitOID"),
    "WI": ("WI記録", "開始時のHEAD"),
}


def _review_basis_error(reason: str) -> ActionableError:
    """基準名と基準別の追加入力の誤りを、正しい指定方法とともに返す。"""
    return ActionableError(
        f"exec-reviewの起動入力が不成立です: {reason}。委譲先は起動していない。",
        next_action=(
            "`レビュー基準`には`計画`・`CI記録`・`WI`の基準名だけを読点（`、`または`,`）で区切って書き、"
            "資料は基準ごとの別入力へ渡して`start`を再発行する。"
            "`計画`は`計画`に計画ファイルの絶対パス、`CI記録`は`CI記録`・`修正系列の開始時のHEAD`・`原因commitOID`、"
            "`計画`を含まない`WI`は`WI記録`・`開始時のHEAD`を渡す"
            "（例: `レビュー基準: 計画`と`計画: /abs/plan.md`）。"
            "入力の定義は`share/exec-review.subagent.md`「入力」にある"
        ),
    )


def _check_review_bases(params: Mapping[str, str]) -> None:
    """`レビュー基準`の基準名と、選んだ基準が求める追加入力を資料検査の前に確かめる。

    基準名へパスや説明を連結した値は、別入力の資料が無いまま起動して担当が初めて不足に気付くため拒否する。
    """
    value = params.get("レビュー基準", "").strip()
    items = [item.strip() for item in re.split(r"[、,，､]", value)]
    unknown = [item for item in items if item not in _REVIEW_BASES]
    if not value or unknown:
        shown = "、".join(f"`{item}`" for item in unknown) or "空の値"
        raise _review_basis_error(f"`レビュー基準`に基準名以外の値があります: {shown}")
    if len(set(items)) != len(items):
        raise _review_basis_error("`レビュー基準`に同じ基準名が重複しています")
    for basis in items:
        if basis == "WI" and "計画" in items:
            continue
        missing = [name for name in _REVIEW_BASIS_INPUTS[basis] if not params.get(name, "").strip()]
        if missing:
            names = "、".join(f"`{name}`" for name in missing)
            raise _review_basis_error(f"`レビュー基準`の`{basis}`に必要な入力がありません: {names}")
    if "計画" in items:
        _material_paths("計画", params["計画"])


def _check_exec_review_materials(params: Mapping[str, str]) -> None:
    """基準と追加入力を確かめてから、新形式AWIの条件表を起動前に構造だけ検査する。内容の評価はレビュー担当が担う。"""
    _check_review_bases(params)
    names: set[str] = set()
    if plan := params.get("計画"):
        for path in _material_paths("計画", plan):
            names.update(commit_mapping.plan_allowed_wis(path.read_text(encoding="utf-8"), path))
    record = params.get("WI記録", "")
    if record and "\n" not in record and pathlib.Path(record).is_absolute():
        record = _material_paths("WI記録", record)[0].read_text(encoding="utf-8")
    names.update(re.findall(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md", record))
    if original := params.get("評価用WI原本"):
        try:
            if not pathlib.Path(original).is_absolute() or "\n" in original:
                raise ValueError("一覧ファイル1件の絶対パスが必要")
            originals = _material_paths("評価用WI原本", original)
            texts = review_contract.read_wi_originals(originals[0], names)
        except ValueError as error:
            raise _material_error("評価用WI原本", str(error)) from error
    else:
        if not names:
            return
        selected, missing = wi_entries.read_named_entries(
            private_notes.default_private_notes(pathlib.Path.home()),
            [(name, name) for name in sorted(names)],
            target_repo=None,
        )
        if missing:
            raise _material_error("WI記録", f"AWI本文を取得できません: {', '.join(missing)}")
        texts = {path.name: text for path, _repo, text, _state, _kind in selected}
    required: dict[str, tuple[int, int]] = {}
    for name, text in texts.items():
        parsed = frontmatter.parse_frontmatter(text)
        sections = {name for name, _body in headings.h2_sections(text)}
        metadata = parsed[0] if parsed is not None else {}
        counts = review_contract.required_counts(
            kind=normalized_wi_type(metadata.get("type")),
            source=metadata.get("source"),
            sections=sections,
            supplements=len(headings.last_h2_section_items(text, "完成条件")),
            reflections=len(headings.last_h2_section_items(text, "反映内容と反映先")),
        )
        if counts is not None:
            required[name] = counts
    if not required:
        return
    reference = "share/exec-review-recording.md「記録する行と要求単位」"

    value = params.get("review_contract", "")
    if not value or "\n" in value or not pathlib.Path(value).is_absolute():
        raise _material_error(
            "review_contract",
            f"{', '.join(required)}: 条件表の実在するファイルの絶対パスが必要。条件表の書式: {reference}",
        )
    try:
        paths = _material_paths("review_contract", value)
        review_contract.validate(paths[0].read_text(encoding="utf-8"), required)
    except ValueError as error:
        raise _material_error("review_contract", f"{', '.join(required)}: {error}。条件表の書式: {reference}") from error


def _check_git_reader_materials(materials: dict[str, Any], current: list[pathlib.Path]) -> None:
    """保存物と同じ比較対象を、Git識別子で受け取る。"""
    repository = materials["repository"]
    paths = materials["paths"]
    if not isinstance(repository, str) or not pathlib.Path(repository).is_absolute():
        raise _material_error("比較資料", "repositoryにGit作業ツリーの絶対パスが必要")
    if not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths):
        raise _material_error("比較資料", "pathsに空でないリポジトリ相対パス配列が必要")
    if len(paths) != len(current) or {pathlib.Path(repository) / path for path in paths} != set(current):
        raise _material_error("比較資料", "pathsが成果物の集合と一致しない")
    # 計画別の判定範囲は、委譲元が`git diff`などで保存した差分ファイルをパスごとに任意で受け取る。
    # 探索担当は読み取り専用のため、契約から帰属を計算させず、保存済みの差分で走査範囲を1通りに決める。
    scopes = materials.get("判定範囲差分", {})
    if not isinstance(scopes, dict) or set(scopes) - set(paths) or any(not isinstance(value, str) for value in scopes.values()):
        raise _material_error(
            "比較資料", "判定範囲差分にはpathsのリポジトリ相対パスから保存ファイルの絶対パスへのオブジェクトが必要"
        )
    for path, scope in scopes.items():
        scope_paths = _material_paths("判定範囲差分", scope)
        if len(scope_paths) != 1:
            raise _material_error("判定範囲差分", "成果物ごとに1件の差分ファイルが必要")
        if error := _patch_target_error(scope_paths[0].read_text(encoding="utf-8"), pathlib.Path(repository) / path):
            raise _material_error("判定範囲差分", error)
    for key in ("base", "head"):
        revision = materials[key]
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", revision):
            raise _material_error("比較資料", f"{key}にcommitの一意なOIDが必要")
        checked = git_command.run(
            ["rev-parse", "--verify", f"{revision}^{{commit}}"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=PREFLIGHT_TIMEOUT,
            check=False,
        )
        if checked.returncode != 0:
            raise _material_error("比較資料", f"{key}のcommitを参照できない: {revision}")
    for path in paths:
        shown = git_command.run(
            ["show", f"{materials['head']}:{path}"],
            cwd=repository,
            capture_output=True,
            timeout=PREFLIGHT_TIMEOUT,
            check=False,
        )
        if shown.returncode != 0 or shown.stdout != (pathlib.Path(repository) / path).read_bytes():
            raise _material_error("比較資料", f"現行本文がheadの同じ版に一致しない: {path}")
