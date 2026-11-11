"""取得済み候補と選定YAMLを読み込み、初回配分との一致を確かめる。"""

from __future__ import annotations

import functools
import json
import pathlib
import typing

import selection_contract
import yaml
from check_selection_common import _FIX_CONTENT, _FIX_PATHS, _FIX_PRIVATE_NOTES, InputError, Violation
from check_selection_lanes import _resume_plan_id

from agent_toolkit._atk.wi import frontmatter as _wi_frontmatter
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._git import remote as _git_remote
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import selection as _selection

_FIX_YAML = (
    "選定結果のYAML構文を直す。文字列の値を単一引用符で囲み、値の中の`'`は`''`と重ねて書き直してから、同じコマンドを再実行する"
)


_FIX_MODEL = (
    "`担当モデル`は`実装担当`か`実行レビュー担当`のキーごとに"
    "`<claude|codex|agy>:<model>/<effort>`か`opencode:<provider>/<model>@<effort>`の値へ直す"
)


_NEXT_SOURCE_MISMATCH = "一致しない欄を、元のWIのfrontmatterの`depends_on`・`target_repo`と選定の`再開位置`が示す計画の値へ直す"


def _check_allocation_sources(selection: dict[str, object], private_notes: pathlib.Path) -> list[Violation]:
    """依存と再開計画の型付き根拠を元のWIのfrontmatterと選定の再開位置へ対応付け、一致しない欄と両方の値を示す。"""
    data = typing.cast(dict[str, typing.Any], selection[_selection.INITIAL_ALLOCATION_KEY])
    items = {
        item[_selection.WI_KEY]: item for item in typing.cast(list[dict[str, typing.Any]], _selection.decisions(selection))
    }
    writes, stages = selection_contract.current_write_stages(selection)
    sources: dict[str, dict[str, typing.Any]] = {}
    errors: list[Violation] = []
    for group in data["不可分成分"]:
        for edge in group["結合条件"]:
            first, second, kind, evidence = (edge[key] for key in ("WI1", "WI2", "種別", "根拠"))
            # 書込重複は成立の判定が選定の現在値を使うため、比べる元入力を持たない。
            if (
                kind == "書込重複"
                or first not in items
                or second not in items
                or selection_contract.allocation_edge_failures(edge, writes, stages)
            ):
                continue
            mismatches: list[str] = []
            if kind == "依存":
                for name in (first, second):
                    if name not in sources:
                        try:
                            source = _plan_file.find_wi_source(name, private_notes)
                            parsed = None
                            if source is not None:
                                parsed = _wi_frontmatter.parse_frontmatter(source.read_text(encoding="utf-8"))
                        except (OSError, UnicodeDecodeError) as error:
                            raise InputError(
                                f"依存元のWI本文を読み込めない: {name}: {error}", next_action=_FIX_PRIVATE_NOTES
                            ) from error
                        sources[name] = parsed[0] if parsed else {}
                source_name = evidence["依存元"]
                actual = sources[source_name].get("depends_on", [])
                if actual != evidence["depends_on"]:
                    mismatches.append(f"depends_on: 根拠={evidence['depends_on']}、{source_name}のfrontmatter={actual}")
                for index, name in enumerate((first, second), 1):
                    repository = sources[name].get("target_repo")
                    resolved = _git_remote.resolve_repo_identifier(repository) if isinstance(repository, str) else None
                    if resolved != evidence[f"リポジトリ{index}"]:
                        mismatches.append(
                            f"リポジトリ{index}: 根拠={evidence[f'リポジトリ{index}']}、{name}のtarget_repo={resolved}"
                        )
            else:
                for index, name in enumerate((first, second), 1):
                    planned = _resume_plan_id(items[name].get("再開位置"))
                    recorded = pathlib.PurePosixPath(evidence[f"計画{index}"]).name
                    if planned != recorded:
                        mismatches.append(f"計画{index}: 根拠の計画={recorded}、{name}の再開位置の計画={planned}")
            if mismatches:
                errors.append(
                    Violation(
                        f"初回配分: {first}と{second}の{kind}の結合根拠が元入力と一致しない: {'; '.join(mismatches)}",
                        next_action=_NEXT_SOURCE_MISMATCH,
                    )
                )
    return errors


def _candidate_records(path: pathlib.Path) -> list[dict[str, typing.Any]]:
    """取得済みwi listのJSON LinesまたはJSON配列を読む。新しい候補照会は行わない。"""
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = [json.loads(line) for line in split_lines(text) if line.strip()]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ValueError("候補情報はJSON LinesまたはJSON objectの配列で指定する")
        return data
    except (OSError, UnicodeError, ValueError) as error:
        raise InputError(
            f"取得済み候補情報を読めない: {path}: {error}",
            next_action="--with-stalenessで取得済みのwi listの保存先を--candidate-fileへ指定する",
        ) from error


def _generate_selection(judgments: object, candidates: pathlib.Path, *, additional: bool) -> dict[str, object]:
    @functools.cache
    def metadata(name: str) -> dict[str, typing.Any]:
        source = _plan_file.find_wi_source(name, _plan_file.private_notes_root())
        if source is None:
            raise InputError(f"生成に使うWI本文が無い: {name}", next_action=_FIX_PRIVATE_NOTES)
        parsed = _wi_frontmatter.parse_frontmatter(source.read_text(encoding="utf-8"))
        header = dict(parsed[0]) if parsed else {}
        repo = header.get("target_repo")
        if isinstance(repo, str):
            header["target_repo"] = _git_remote.resolve_repo_identifier(repo)
        return header

    try:
        return selection_contract.generate_selection(
            judgments, _candidate_records(candidates), metadata, _resume_plan_id, additional=additional
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise InputError(
            str(error), next_action="候補情報と意味判断をselection-format.mdの生成入力へそろえて再実行する"
        ) from error


def load_selection(
    selection_file: pathlib.Path,
    *,
    additional: bool = False,
    candidate_file: pathlib.Path | None = None,
) -> dict[str, object]:
    """選定結果を読み、YAML構文、欄名、必須の欄および値の型を確かめて返す。

    ファイルを開けない失敗はパスの誤りとして、読めた内容の誤りは選定結果の修正として、別の次の操作を付けて送出する。
    """
    try:
        text = selection_file.read_text(encoding="utf-8")
    except OSError as error:
        raise InputError(f"選定結果を読み込めない: {selection_file}: {error}", next_action=_FIX_PATHS) from error
    except UnicodeDecodeError as error:
        raise InputError(
            f"選定結果がUTF-8ではない: {selection_file}: {error}",
            next_action="選定結果をUTF-8で保存し直してから、同じコマンドを再実行する",
        ) from error
    try:
        selection = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise InputError(f"選定結果のYAML構文が不正: {selection_file}: {error}", next_action=_FIX_YAML) from error
    if candidate_file is not None:
        selection = _generate_selection(selection, candidate_file, additional=additional)
    elif not additional:
        _drop_write_scope_reasons(selection)
    errors, model_errors = selection_contract.structure_errors(selection)
    if isinstance(selection, dict) and not additional and _selection.INITIAL_ALLOCATION_KEY not in selection:
        errors.append(
            "初回配分がない。初回候補WI・レーン割当・不可分成分の構成WIと結合条件を、"
            "元候補と初回選定の記録から補い、selection-format.mdの初回配分の形式で再実行する"
        )
    if errors or model_errors:
        next_action = f"{_FIX_MODEL}。{_FIX_CONTENT}" if model_errors else _FIX_CONTENT
        raise InputError(
            "\n".join([f"選定結果の内容が不正: {selection_file}", *model_errors, *errors]), next_action=next_action
        )
    return typing.cast(dict[str, object], selection)


def _drop_write_scope_reasons(selection: object) -> None:
    """旧版が保存した選定結果の`書込範囲の理由`を捨てる。

    同欄は重なりの判定と配分に寄与しないため撤去した。保存済みの選定結果をそのまま読めるよう、
    読取時に除いて保存時にも出力しない。新しく書く入力（生成入力・追加入力）の同欄は未知の欄として拒否する。
    """
    if not isinstance(selection, dict):
        return
    items = selection.get(_selection.DECISIONS_KEY, selection.get("decisions"))
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            item.pop(_selection.WRITE_SCOPE_REASONS_KEY, None)
